#!/usr/bin/env python3
"""Build a constrained U.S. visa appointment-profile fill plan.

The plan deliberately stops short of authentication, fee payment, appointment
slot selection, dependent creation, and final booking confirmation.
"""

import re

from .ds160_language import translate_ds160_value
from .ds160_value_validation import canonicalize_ds160_value, field_value_is_usable


APPOINTMENT_ALLOWED_DOMAIN = "www.usvisascheduling.com"
APPOINTMENT_START_URL = "https://www.usvisascheduling.com/"

LOCATION_ALIASES = {
    "北京": "BEIJING",
    "北京使馆": "BEIJING",
    "上海": "SHANGHAI",
    "上海领馆": "SHANGHAI",
    "广州": "GUANGZHOU",
    "广州领馆": "GUANGZHOU",
    "沈阳": "SHENYANG",
    "沈阳领馆": "SHENYANG",
    "武汉": "WUHAN",
    "武汉领馆": "WUHAN",
}

VISA_CLASS_META = {
    "b1b2": ("B1/B2", ["B1/B2", "BUSINESS", "TOURISM"]),
    "f1": ("F-1", ["F-1", "F1", "STUDENT"]),
    "f2": ("F-2", ["F-2", "F2", "STUDENT DEPENDENT"]),
    "j1": ("J-1", ["J-1", "J1", "EXCHANGE VISITOR"]),
    "j2": ("J-2", ["J-2", "J2", "EXCHANGE DEPENDENT"]),
}

POST_VISA_CATEGORY_META = {
    "b1b2": "BUSINESS / TOURISM",
    "f1": "STUDENTS - OTHER STUDENTS",
    "f2": "STUDENTS - DEPENDENTS",
    "j1": "EXCHANGE VISITORS",
    "j2": "EXCHANGE VISITOR DEPENDENTS",
}


def _clean(value, limit=500):
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _field_map(payload):
    return {
        str(item.get("id") or ""): item
        for item in (payload.get("extractedFields") or [])
        if isinstance(item, dict)
    }


def _translated_value(value, field_id, context, *, preserve_native=False):
    prepared = translate_ds160_value(
        value,
        field_id=field_id,
        context=context,
        preserve_native=preserve_native,
    )
    normalized = canonicalize_ds160_value(field_id, prepared.get("value"))
    if not field_value_is_usable(field_id, normalized):
        return ""
    return _clean(normalized)


def _field_value(fields, field_id):
    source = fields.get(field_id) or {}
    return _translated_value(
        source.get("value"), field_id, source.get("label") or field_id
    )


def _field_original_value(fields, field_id):
    source = fields.get(field_id) or {}
    return _clean(source.get("originalValue") or source.get("value"))


def _prep_value(preparation, key, field_id=None):
    value = _clean(preparation.get(key))
    if not value:
        return ""
    if not field_id:
        return value
    return _translated_value(value, field_id, key)


def _visa_id(value):
    normalized = _clean(value).lower().replace("-", "").replace("/", "")
    if normalized.startswith("b1b2") or normalized.startswith("b2"):
        return "b1b2"
    for visa_id in ("f1", "f2", "j1", "j2"):
        if normalized.startswith(visa_id):
            return visa_id
    return ""


def _location(value):
    cleaned = _clean(value, 100)
    return LOCATION_ALIASES.get(cleaned, cleaned.upper())


def _action(action_id, label, kind, value, **extra):
    action = {
        "id": action_id,
        "label": label,
        "kind": kind,
        "value": _clean(value),
    }
    action.update(extra)
    return action


def appointment_values(payload):
    fields = _field_map(payload)
    preparation = payload.get("appointmentPreparation") or {}
    visa_id = _visa_id(payload.get("visaType"))
    visa_class, visa_terms = VISA_CLASS_META.get(visa_id, ("", []))
    source_phone = _prep_value(
        preparation, "primaryPhone", "contact.primaryPhone"
    ) or _field_value(fields, "contact.primaryPhone")
    compact_phone = re.sub(r"[\s()\-]", "", source_phone)
    mobile_code = _prep_value(preparation, "mobilePhoneCountryCode") or "+86"
    mobile_number = _prep_value(preparation, "mobilePhone")
    if not mobile_number and compact_phone:
        mobile_number = compact_phone[len(mobile_code):] \
            if compact_phone.startswith(mobile_code) else compact_phone
    values = {
        "accountReady": bool(preparation.get("accountReady")),
        "portalUsername": _prep_value(preparation, "portalUsername"),
        "surname": _field_value(fields, "personal.surname"),
        "givenNames": _field_value(fields, "personal.givenNames"),
        "dateOfBirth": _field_value(fields, "personal.dateOfBirth"),
        "countryOfBirth": _prep_value(preparation, "countryOfBirth")
        or _field_value(fields, "personal.birthCountry"),
        "nationality": _field_value(fields, "personal.nationality"),
        "passportNumber": _field_value(fields, "passport.number"),
        "passportIssueDate": _field_value(fields, "passport.issueDate"),
        "passportExpiration": _field_value(fields, "passport.expiration"),
        "ds160ConfirmationNumber": _prep_value(
            preparation, "ds160ConfirmationNumber"
        ).upper(),
        "schedulingEmail": _prep_value(
            preparation, "schedulingEmail", "contact.email"
        ) or _field_value(fields, "contact.email"),
        "contactEmail": _prep_value(
            preparation, "contactEmail", "contact.email"
        ) or _field_value(fields, "contact.email"),
        "preferredLanguage": _prep_value(preparation, "preferredLanguage") or "zh-CN",
        "countryOfApplication": (
            _prep_value(preparation, "countryOfApplication") or "CHINA"
        ).upper(),
        "homePhoneCountryCode": _prep_value(
            preparation, "homePhoneCountryCode"
        ) or "+86",
        "homePhone": _prep_value(preparation, "homePhone"),
        "mobilePhoneCountryCode": mobile_code,
        "mobilePhone": mobile_number,
        "primaryPhone": source_phone,
        "mailingStreet": _prep_value(preparation, "mailingStreet")
        or _field_original_value(fields, "contact.homeStreet1")
        or _field_original_value(fields, "contact.homeAddress"),
        "mailingCity": _prep_value(preparation, "mailingCity")
        or _field_original_value(fields, "contact.homeCity"),
        "mailingState": _prep_value(preparation, "mailingState")
        or _field_original_value(fields, "contact.homeRegion"),
        "mailingPostalCode": _prep_value(preparation, "mailingPostalCode")
        or _field_value(fields, "contact.homePostalCode"),
        "applicationLocation": _location(preparation.get("applicationLocation")),
        "visaClass": visa_class,
        "visaTerms": visa_terms,
        "postVisaCategory": _prep_value(preparation, "postVisaCategory")
        or POST_VISA_CATEGORY_META.get(visa_id, ""),
        "visaPriority": _prep_value(preparation, "visaPriority") or "REGULAR",
        "sevisId": _prep_value(
            preparation, "sevisId", "education.sevisId"
        ) or _field_value(fields, "education.sevisId"),
        "schoolName": _prep_value(
            preparation, "schoolName", "education.schoolName"
        ) or _field_value(fields, "education.schoolName"),
        "schoolZipCode": _prep_value(
            preparation, "schoolZipCode", "education.schoolPostalCode"
        ) or _field_value(fields, "education.schoolPostalCode"),
        "deliveryOption": _prep_value(preparation, "deliveryOption").upper(),
        "deliveryStreet1": _prep_value(preparation, "deliveryStreet1"),
        "deliveryStreet2": _prep_value(preparation, "deliveryStreet2"),
        "deliveryStreet3": _prep_value(preparation, "deliveryStreet3"),
        "deliveryCity": _prep_value(preparation, "deliveryCity"),
        "deliveryState": _prep_value(preparation, "deliveryState"),
        "deliveryPostalCode": _prep_value(preparation, "deliveryPostalCode"),
        "pickupLocation": _prep_value(preparation, "pickupLocation"),
        "visaId": visa_id,
    }
    return values


def appointment_preflight_issues(payload):
    values = appointment_values(payload)
    required = (
        ("accountReady", "可用的预约账户"),
        ("portalUsername", "预约系统用户名"),
        ("surname", "护照英文姓"),
        ("givenNames", "护照英文名"),
        ("dateOfBirth", "出生日期"),
        ("countryOfBirth", "出生国家"),
        ("nationality", "国籍"),
        ("passportNumber", "护照号码"),
        ("ds160ConfirmationNumber", "DS-160 确认号"),
        ("schedulingEmail", "注册邮箱"),
        ("contactEmail", "联系邮箱"),
        ("preferredLanguage", "系统界面语言"),
        ("countryOfApplication", "递交国家"),
        ("homePhone", "家庭电话"),
        ("mobilePhone", "手机号码"),
        ("mailingStreet", "中文邮寄街道地址"),
        ("mailingCity", "中文邮寄城市"),
        ("mailingState", "中文邮寄省份"),
        ("mailingPostalCode", "邮寄地址邮编"),
        ("applicationLocation", "使领馆"),
        ("postVisaCategory", "预约系统签证细类"),
        ("visaPriority", "签证优先级"),
        ("deliveryOption", "护照递送方式"),
        ("visaClass", "签证类别"),
    )
    issues = [
        {"id": key, "label": label}
        for key, label in required
        if not values.get(key)
    ]
    confirmation = values.get("ds160ConfirmationNumber") or ""
    if confirmation and not re.fullmatch(r"[A-Z0-9]{8,20}", confirmation):
        issues.append({
            "id": "ds160ConfirmationNumber",
            "label": "DS-160 确认号格式（仅允许 8-20 位英文字母和数字）",
        })
    email = values.get("schedulingEmail") or ""
    if email and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        issues.append({"id": "schedulingEmail", "label": "有效的注册邮箱"})
    contact_email = values.get("contactEmail") or ""
    if contact_email and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", contact_email):
        issues.append({"id": "contactEmail", "label": "有效的联系邮箱"})
    for key, label in (
        ("homePhone", "家庭电话不含国家代码"),
        ("mobilePhone", "手机号码不含国家代码"),
    ):
        phone = values.get(key) or ""
        if phone and (phone.startswith(("+", "00")) or len(re.sub(r"\D", "", phone)) < 5):
            issues.append({"id": key, "label": label})
    if values.get("visaId") in {"f1", "f2", "j1", "j2"}:
        for key, label in (
            ("sevisId", "SEVIS ID"),
            ("schoolName", "学校或项目名称"),
            ("schoolZipCode", "学校或项目邮编"),
        ):
            if not values.get(key):
                issues.append({"id": key, "label": label})
    if values.get("deliveryOption") == "PREMIUM_DELIVERY":
        for key, label in (
            ("deliveryStreet1", "递送街道地址"),
            ("deliveryCity", "递送城市"),
            ("deliveryState", "递送省份"),
            ("deliveryPostalCode", "递送地址邮编"),
        ):
            if not values.get(key):
                issues.append({"id": key, "label": label})
    elif values.get("deliveryOption") in {"PREMIUM_LOCATION", "PICK_UP"}:
        if not values.get("pickupLocation"):
            issues.append({"id": "pickupLocation", "label": "领取服务点"})
    return issues


def build_appointment_workflow(payload):
    """Create fill-only pages for the post-DS-160 scheduling profile."""
    values = appointment_values(payload)

    profile_actions = [
        _action(
            "appointment.profile.contact_email", "Contact Email", "text",
            values["contactEmail"], labelTerms=["Contact Email"],
            controlHints=["contactemail"], optionalOnPage=True,
        ),
        _action(
            "appointment.profile.language", "Preferred Language", "select_text",
            values["preferredLanguage"], labelTerms=["Preferred Language"],
            optionTerms=[values["preferredLanguage"]],
            optionAlternatives=["中文", "CHINESE", "CHINA"]
            if values["preferredLanguage"].lower().startswith("zh") else ["ENGLISH"],
            controlHints=["preferredlanguage", "language"], optionalOnPage=True,
        ),
        _action(
            "appointment.profile.country", "Country", "select_text",
            values["countryOfApplication"], labelTerms=["Country", "Country of Application"],
            optionTerms=[values["countryOfApplication"]], optionAlternatives=["CHINA"],
            controlHints=["countryofapplication", "country"], optionalOnPage=True,
        ),
    ]

    applicant_actions = [
        _action(
            "appointment.country_applying", "Country from which you are applying",
            "select_text", values["countryOfApplication"],
            labelTerms=["Country from which you are applying", "Country of Application"],
            optionTerms=[values["countryOfApplication"]], optionAlternatives=["CHINA"],
            controlHints=["countryapplying", "countryofapplication"], optionalOnPage=True,
        ),
        _action(
            "appointment.surname", "Surname", "text", values["surname"],
            labelTerms=["Surname", "Last Name", "Family Name"],
            controlHints=["surname", "lastname", "familyname"],
        ),
        _action(
            "appointment.given_names", "Given Name", "text", values["givenNames"],
            labelTerms=["Given Name", "First Name"],
            controlHints=["givenname", "firstname"],
        ),
        _action(
            "appointment.date_of_birth", "Date of Birth", "date",
            values["dateOfBirth"], labelTerms=["Date of Birth", "Birth Date"],
            controlHints=["dateofbirth", "birthdate", "dob"],
        ),
        _action(
            "appointment.country_of_birth", "Country of Birth", "select_text",
            values["countryOfBirth"], labelTerms=["Country of Birth", "Birth Country"],
            optionTerms=[values["countryOfBirth"]], optionAlternatives=[values["countryOfBirth"]],
            controlHints=["countryofbirth", "birthcountry"],
        ),
        _action(
            "appointment.nationality", "Nationality", "select_text",
            values["nationality"], labelTerms=["Nationality", "Country of Nationality"],
            optionTerms=[values["nationality"]],
            controlHints=["nationality", "countryofnationality"],
        ),
        _action(
            "appointment.passport_number", "Passport Number", "text",
            values["passportNumber"], labelTerms=["Passport Number"],
            controlHints=["passportnumber", "passport_no"],
        ),
        _action(
            "appointment.passport_issue_date", "Passport Issuance Date", "date",
            values["passportIssueDate"], labelTerms=["Passport Issuance Date", "Issue Date"],
            controlHints=["passportissuedate", "issuancedate"], optionalOnPage=True,
        ),
        _action(
            "appointment.passport_expiration", "Passport Expiration Date", "date",
            values["passportExpiration"], labelTerms=["Passport Expiration Date", "Expiry Date"],
            controlHints=["passportexpiration", "passportexpiry"], optionalOnPage=True,
        ),
        _action(
            "appointment.email", "Email Address", "text", values["schedulingEmail"],
            labelTerms=["Email Address", "Email"], controlHints=["emailaddress", "email"],
            optionalOnPage=True,
        ),
        _action(
            "appointment.home_phone_code", "Home Phone Country Code", "select_text",
            values["homePhoneCountryCode"], labelTerms=["Home Phone", "Home Phone Country"],
            optionTerms=[values["homePhoneCountryCode"]],
            optionAlternatives=[values["homePhoneCountryCode"], "CHINA"],
            controlHints=["homephonecountry", "homecountrycode"], optionalOnPage=True,
        ),
        _action(
            "appointment.home_phone", "Home Phone", "text", values["homePhone"],
            labelTerms=["Home Phone"], controlHints=["homephone", "hometelephone"],
        ),
        _action(
            "appointment.mobile_phone_code", "Mobile Phone Country Code", "select_text",
            values["mobilePhoneCountryCode"], labelTerms=["Mobile Phone", "Mobile Phone Country"],
            optionTerms=[values["mobilePhoneCountryCode"]],
            optionAlternatives=[values["mobilePhoneCountryCode"], "CHINA"],
            controlHints=["mobilephonecountry", "mobilecountrycode"], optionalOnPage=True,
        ),
        _action(
            "appointment.mobile_phone", "Mobile Phone", "text", values["mobilePhone"],
            labelTerms=["Mobile Phone"], controlHints=["mobilephone", "cellphone"],
        ),
        _action(
            "appointment.mailing_street", "Mailing Street", "text", values["mailingStreet"],
            labelTerms=["Mailing Street", "Mailing Address"],
            controlHints=["mailingstreet", "mailingaddress"],
        ),
        _action(
            "appointment.mailing_city", "Mailing City", "text", values["mailingCity"],
            labelTerms=["Mailing City"], controlHints=["mailingcity"],
        ),
        _action(
            "appointment.mailing_state", "Mailing State/Province", "text", values["mailingState"],
            labelTerms=["Mailing State", "Mailing Province"],
            controlHints=["mailingstate", "mailingprovince"],
        ),
        _action(
            "appointment.mailing_postal", "Mailing Zip/Postal Code", "text",
            values["mailingPostalCode"], labelTerms=["Mailing Zip", "Mailing Postal Code"],
            controlHints=["mailingzip", "mailingpostal"],
        ),
    ]

    visa_actions = [
        _action(
            "appointment.visa_class", "Visa Class", "select_text", values["visaClass"],
            labelTerms=["Visa Class", "Visa Type", "Purpose of Travel"],
            optionTerms=[values["visaClass"]],
            optionAlternatives=values["visaTerms"] or [values["visaClass"]],
            controlHints=["visaclass", "visatype", "visa_category"],
        ),
        _action(
            "appointment.location", "Application Location", "select_text",
            values["applicationLocation"],
            labelTerms=["Application Location", "Consular Location", "Embassy", "Consulate"],
            optionTerms=[values["applicationLocation"]],
            controlHints=["applicationlocation", "consularlocation", "post"],
        ),
        _action(
            "appointment.post_visa_category", "Post Visa Category", "select_text",
            values["postVisaCategory"], labelTerms=["Post Visa Category"],
            optionTerms=[values["postVisaCategory"]],
            optionAlternatives=[values["postVisaCategory"], values["visaClass"]] + values["visaTerms"],
            controlHints=["postvisacategory", "postcategory"],
        ),
        _action(
            "appointment.visa_priority", "Visa Priority", "select_text",
            values["visaPriority"], labelTerms=["Visa Priority"],
            optionTerms=[values["visaPriority"]], optionAlternatives=["REGULAR"],
            controlHints=["visapriority", "priority"], optionalOnPage=True,
        ),
    ]
    confirmation_actions = [
        _action(
            "appointment.confirmation.ds160", "DS-160 Confirmation Number", "text",
            values["ds160ConfirmationNumber"],
            labelTerms=["DS-160 Confirmation Number", "DS160 Confirmation Number", "DS-160 Number"],
            controlHints=["ds160", "ds_160", "confirmationnumber"],
        ),
    ]
    if values["visaId"] in {"f1", "f2", "j1", "j2"}:
        confirmation_actions.extend([
            _action(
                "appointment.confirmation.sevis_id", "SEVIS ID", "text", values["sevisId"],
                labelTerms=["SEVIS ID", "SEVIS Number", "SEVIS Information"],
                controlHints=["sevis", "sevisid"],
            ),
            _action(
                "appointment.confirmation.school_name", "School or Program Name", "text",
                values["schoolName"],
                labelTerms=["School Name", "University Name", "Program Name", "School Details"],
                controlHints=["schoolname", "universityname", "programname"],
            ),
            _action(
                "appointment.confirmation.school_zip", "University Zip Code", "text",
                values["schoolZipCode"], labelTerms=["University Zip Code", "School Zip Code"],
                controlHints=["universityzipcode", "schoolzipcode", "schoolpostal"],
            ),
        ])

    delivery_actions = []
    if values["deliveryOption"] == "PREMIUM_DELIVERY":
        delivery_actions = [
            _action(
                "appointment.delivery.street1", "Document Delivery Street", "text",
                values["deliveryStreet1"], labelTerms=["Document Delivery Street"],
                controlHints=["documentdeliverystreet", "deliverystreet"],
            ),
            _action(
                "appointment.delivery.street2", "Document Delivery Street 2", "text",
                values["deliveryStreet2"], labelTerms=["Document Delivery Street 2"],
                controlHints=["documentdeliverystreet2", "deliverystreet2"], optionalOnPage=True,
            ),
            _action(
                "appointment.delivery.street3", "Document Delivery Street 3", "text",
                values["deliveryStreet3"], labelTerms=["Document Delivery Street 3"],
                controlHints=["documentdeliverystreet3", "deliverystreet3"], optionalOnPage=True,
            ),
            _action(
                "appointment.delivery.city", "Document Delivery City", "text",
                values["deliveryCity"], labelTerms=["Document Delivery City"],
                controlHints=["documentdeliverycity", "deliverycity"],
            ),
            _action(
                "appointment.delivery.state", "Document Delivery State", "text",
                values["deliveryState"], labelTerms=["Document Delivery State"],
                controlHints=["documentdeliverystate", "deliverystate"],
            ),
            _action(
                "appointment.delivery.postal", "Document Delivery Postal Code", "text",
                values["deliveryPostalCode"], labelTerms=["Document Delivery Postal Code"],
                controlHints=["documentdeliverypostal", "deliverypostal"],
            ),
        ]

    pages = [
        {
            "key": "appointment_profile",
            "label": "Profile",
            "exactPaths": [
                "/en-US/profile/",
                "/zh-CN/profile/",
                "/profile/",
            ],
            "urlPatterns": ["/profile"],
            "actions": [item for item in profile_actions if item.get("value")],
            "allowNext": False,
            "manualReview": True,
            "stopReason": "个人档案中的联系邮箱、界面语言和递交国家已写入，请顾问核对后继续。",
        },
        {
            "key": "appointment_applicant_details",
            "label": "Applicant Details",
            "exactPaths": [
                "/en-US/applicant_details/",
                "/zh-CN/applicant_details/",
                "/applicant_details/",
            ],
            "actions": [item for item in applicant_actions if item.get("value")],
            "allowNext": False,
            "manualReview": True,
            "stopReason": "申请人、联系方式、中文邮寄地址和护照资料已写入，请顾问核对后继续。",
        },
        {
            "key": "appointment_visa_options",
            "label": "Visa Options",
            "exactPaths": [
                "/en-US/applicant_details/application_details/",
                "/zh-CN/applicant_details/application_details/",
                "/applicant_details/application_details/",
            ],
            "actions": [item for item in visa_actions if item.get("value")],
            "allowNext": False,
            "manualReview": True,
            "stopReason": "使领馆、签证细类、优先级与 Visa Class 已写入，请顾问核对后继续。",
        },
        {
            "key": "appointment_confirmation",
            "label": "DS-160 / SEVIS Information",
            "exactPaths": [],
            "urlPatterns": ["confirmation", "sevis", "additional_visa_options"],
            "actions": [item for item in confirmation_actions if item.get("value")],
            "allowNext": False,
            "manualReview": True,
            "stopReason": "DS-160 与适用的 SEVIS、学校资料已写入，请逐字符核对后继续。",
        },
        {
            "key": "appointment_delivery",
            "label": "Document Delivery",
            "exactPaths": [],
            "urlPatterns": ["delivery"],
            "actions": [item for item in delivery_actions if item.get("value")],
            "allowNext": False,
            "manualReview": True,
            "stopReason": "快递地址已写入。递送方式和可能产生的费用仍由顾问在官网确认。",
        },
    ]
    pages = [page for page in pages if page["actions"]]
    return {
        "version": 1,
        "workflowType": "appointment",
        "page": "workflow",
        "targetUrl": APPOINTMENT_START_URL,
        "pages": pages,
        "totalFields": sum(len(page["actions"]) for page in pages),
        "autoNext": False,
        "clickSave": False,
        "clickNext": False,
        "missingRequired": appointment_preflight_issues(payload),
    }
