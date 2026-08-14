"""V2-only branch preflight for fields CEAC requires after postback."""


def _normalized(value):
    return str(value or "").strip().casefold().replace("-", "_").replace(" ", "_")


def job_preflight_issues(payload):
    """Return user-facing missing-field labels without exposing field values."""
    fields = [
        item
        for item in list((payload or {}).get("fields") or ())
        if isinstance(item, dict)
    ]
    values = {
        str(item.get("id") or "").strip().casefold(): item.get("value")
        for item in fields
        if str(item.get("id") or "").strip()
    }

    def value_for_suffix(suffix):
        normalized_suffix = str(suffix).casefold()
        for field_id, value in values.items():
            if field_id.endswith(normalized_suffix):
                return value
        return ""

    def has_id_parts(*parts):
        normalized_parts = tuple(str(part).casefold() for part in parts)
        return any(
            all(part in field_id for part in normalized_parts)
            for field_id in values
        )

    issues = []

    # Passport fields are planned as one route-scoped page.  If any field for
    # that page exists, require the CEAC controls that cannot be represented by
    # a "Does Not Apply" choice.  In particular, CEAC's issuing-city control is
    # mandatory even though an upstream passport extract may contain only a
    # province.  Failing here keeps the browser closed instead of letting a
    # half-filled Passport page fail after Next.
    has_passport_page = has_id_parts(".passport.")
    if has_passport_page:
        passport_requirements = (
            ((".passport.issuecity",), "护照签发城市"),
            ((".passport.issuedate",), "护照签发日期"),
        )
        for parts, label in passport_requirements:
            if not has_id_parts(*parts):
                issues.append(label)

    education_answer = _normalized(
        value_for_suffix(".work.education_secondary_or_above")
    )
    if education_answer in {"yes", "true", "y", "1"}:
        education_requirements = (
            ((".work.education.record.line1.",), "教育机构地址第一行"),
            ((".work.education.record.city.",), "教育机构城市"),
            ((".work.education.record.country.",), "教育机构国家/地区"),
        )
        for parts, label in education_requirements:
            if not has_id_parts(*parts):
                issues.append(label)

    specific_plans = _normalized(
        value_for_suffix(".travel.specific_plans")
    )
    if specific_plans in {"no", "false", "n", "0"}:
        if not has_id_parts(".travel.arrivaldate"):
            issues.append("预计抵达日期")
        if not has_id_parts(".travel.stayduration"):
            issues.append("预计停留时长")

    payer = _normalized(value_for_suffix(".travel.payer"))
    # CEAC asks no payer follow-up questions for SELF, PRESENT EMPLOYER,
    # or EMPLOYER IN THE U.S. Company/contact details are required only for
    # the explicit OTHER COMPANY/ORGANIZATION branch.
    if payer in {"other_organization"}:
        requirements = (
            (("payerorganization",), "付款公司或机构名称"),
            (("payerphone",), "付款机构电话"),
            (("payeraddress", "line1"), "付款机构地址第一行"),
            (("payeraddress", "city"), "付款机构地址城市"),
            (("payeraddress", "region"), "付款机构地址省/州"),
            (("payeraddress", "postalcode"), "付款机构地址邮编"),
            (("payeraddress", "country"), "付款机构地址国家/地区"),
        )
        for parts, label in requirements:
            if not has_id_parts(*parts):
                issues.append(label)

    return list(dict.fromkeys(issues))


def require_job_preflight(payload):
    issues = job_preflight_issues(payload)
    if not issues:
        return
    raise ValueError(
        "V2 资料预检未通过："
        + "、".join(issues)
        + "。这些字段属于当前 CEAC 分支的必填项；"
        "请回到 DocFlow 补齐并确认后重新准备任务。"
    )
