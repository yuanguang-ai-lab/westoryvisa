#!/usr/bin/env python3
"""DS-160 client intake schema owned by the backend."""
"""Versioned static intake fields shared by the client form and case review."""


INTAKE_SCHEMA_VERSION = "ds160-bfj-intake-2026-07-17-v4"

ALL_VISA_TYPES = ["b1b2", "f1", "f2", "j1", "j2"]
F_VISA_TYPES = ["f1", "f2"]
J_VISA_TYPES = ["j1", "j2"]
FJ_VISA_TYPES = ["f1", "f2", "j1", "j2"]


def intake_field(
    field_id,
    label,
    section,
    *,
    input_type="text",
    placeholder="",
    risk_level="medium",
    hint="",
    choices=None,
    visa_types=None,
    covered_by=None,
    required=True,
):
    value = {
        "id": field_id,
        "label": label,
        "section": section,
        "inputType": input_type,
        "placeholder": placeholder,
        "riskLevel": risk_level,
        "visaTypes": list(visa_types or ALL_VISA_TYPES),
        "required": bool(required),
    }
    if hint:
        value["hint"] = hint
    if choices:
        value["choices"] = list(choices)
    if covered_by:
        value["coveredBy"] = list(covered_by)
    return value


CLIENT_INTAKE_FIELDS = [
    intake_field(
        "application.consulateCountry", "计划申请的使领馆国家 / 地区", "申请信息",
        placeholder="例如：CHINA", risk_level="low",
    ),
    intake_field(
        "application.consulateCity", "计划申请的使领馆城市", "申请信息",
        placeholder="例如：SHANGHAI", risk_level="low",
    ),
    intake_field(
        "application.applicantRole", "本次申请身份", "申请信息", input_type="select",
        choices=[
            {"value": "PRIMARY", "label": "主申请人"},
            {"value": "SPOUSE_DEPENDENT", "label": "配偶家属"},
            {"value": "CHILD_DEPENDENT", "label": "子女家属"},
        ],
    ),
    intake_field(
        "personal.surname", "护照上的英文姓", "基础信息",
        placeholder="例如：ZHANG", risk_level="high",
        hint="请与护照资料页的 Surname 完全一致。",
    ),
    intake_field(
        "personal.givenNames", "护照上的英文名和中间名", "基础信息",
        placeholder="例如：WEI", risk_level="high",
        hint="请与护照资料页的 Given Names 完全一致。",
    ),
    intake_field(
        "personal.nativeName", "完整母语姓名", "基础信息",
        placeholder="例如：张伟", risk_level="medium",
        hint="这是 DS-160 中允许使用母语字符的姓名字段。",
    ),
    intake_field(
        "personal.sex", "出生性别", "基础信息", input_type="select",
        risk_level="high", choices=[
            {"value": "MALE", "label": "男 / Male"},
            {"value": "FEMALE", "label": "女 / Female"},
        ],
    ),
    intake_field(
        "personal.dateOfBirth", "出生日期", "基础信息", input_type="date",
        placeholder="YYYY-MM-DD", risk_level="high",
    ),
    intake_field(
        "personal.birthCity", "出生城市", "基础信息", risk_level="high",
        covered_by=["personal.placeOfBirth"],
    ),
    intake_field(
        "personal.birthRegion", "出生省、州或地区", "基础信息",
        placeholder="不适用时填写 DOES NOT APPLY",
    ),
    intake_field(
        "personal.birthCountry", "出生国家或地区", "基础信息",
        placeholder="例如：CHINA", risk_level="high",
        hint="请使用 DS-160 下拉框中的英文国家名称。",
    ),
    intake_field(
        "personal.nationality", "当前国籍", "基础信息",
        placeholder="例如：CHINA", risk_level="high",
        hint="请使用 DS-160 下拉框中的英文名称；CHN 或 Chinese 会自动整理为 CHINA。",
    ),
    intake_field(
        "personal.nationalId", "本国身份证号码", "基础信息",
        placeholder="没有时填写 DOES NOT APPLY", risk_level="high",
    ),
    intake_field(
        "travel.purposeSummary", "本次赴美真实目的", "旅行信息",
        input_type="textarea", placeholder="请用简单事实说明本次行程目的",
    ),
    intake_field(
        "contact.usStreet1", "在美停留地址 · 街道地址 1", "在美停留地址",
        covered_by=["contact.usAddress"],
        hint="优先填写完整街道地址；系统只在内容过长时使用地址第 2 行。",
    ),
    intake_field(
        "contact.usStreet2", "在美停留地址 · 街道地址 2", "在美停留地址",
        placeholder="没有时可留空", risk_level="low", required=False,
    ),
    intake_field("contact.usCity", "在美停留地址 · 城市", "在美停留地址"),
    intake_field("contact.usState", "在美停留地址 · 州", "在美停留地址"),
    intake_field(
        "contact.usPostalCode", "在美停留地址 · ZIP Code", "在美停留地址",
        placeholder="不知道时可留空", risk_level="low", required=False,
    ),
    intake_field(
        "contact.homeStreet1", "家庭地址 · 街道地址 1", "地址 / 电话 / 社交媒体",
        covered_by=["contact.homeAddress"],
    ),
    intake_field(
        "contact.homeStreet2", "家庭地址 · 街道地址 2", "地址 / 电话 / 社交媒体",
        placeholder="没有时可留空", risk_level="low", required=False,
    ),
    intake_field("contact.homeCity", "家庭地址 · 城市", "地址 / 电话 / 社交媒体"),
    intake_field("contact.homeRegion", "家庭地址 · 省、州或地区", "地址 / 电话 / 社交媒体"),
    intake_field(
        "contact.homePostalCode", "家庭地址 · 邮编", "地址 / 电话 / 社交媒体",
        placeholder="不适用时填写 DOES NOT APPLY", risk_level="low",
    ),
    intake_field(
        "contact.homeCountry", "家庭地址 · 国家或地区", "地址 / 电话 / 社交媒体",
        placeholder="例如：CHINA", hint="请使用 DS-160 下拉框中的英文国家名称。",
    ),
    intake_field(
        "contact.primaryPhone", "主要电话号码", "地址 / 电话 / 社交媒体",
        input_type="tel", placeholder="请包含国家或地区区号",
    ),
    intake_field(
        "contact.secondaryPhone", "次要电话号码", "地址 / 电话 / 社交媒体",
        input_type="tel", placeholder="没有时填写 DOES NOT APPLY", risk_level="low",
    ),
    intake_field(
        "contact.workPhone", "工作电话号码", "地址 / 电话 / 社交媒体",
        input_type="tel", placeholder="没有时填写 DOES NOT APPLY", risk_level="low",
    ),
    intake_field(
        "contact.email", "当前常用 Email", "地址 / 电话 / 社交媒体",
        input_type="email", placeholder="name@example.com",
    ),
    intake_field(
        "passport.number", "护照或旅行证件号码", "护照信息",
        placeholder="请照护照资料页填写", risk_level="high",
    ),
    intake_field(
        "passport.issuingAuthority", "签发国家或机构", "护照信息",
        placeholder="请照护照资料页填写",
    ),
    intake_field("passport.issueCity", "签发城市", "护照信息"),
    intake_field(
        "passport.issueRegion", "签发省、州或地区", "护照信息",
        placeholder="不适用时填写 DOES NOT APPLY", risk_level="low",
    ),
    intake_field(
        "passport.issueCountry", "签发国家或地区", "护照信息",
        placeholder="例如：CHINA", hint="请使用 DS-160 下拉框中的英文国家名称。",
    ),
    intake_field(
        "passport.issueDate", "签发日期", "护照信息", input_type="date",
        placeholder="YYYY-MM-DD",
    ),
    intake_field(
        "passport.expiration", "到期日期", "护照信息", input_type="date",
        placeholder="YYYY-MM-DD；无到期日时注明", risk_level="high",
    ),
    intake_field(
        "education.sevisId", "申请人本人的 SEVIS ID", "SEVIS / 学生信息",
        placeholder="通常以 N00 开头", risk_level="high", visa_types=FJ_VISA_TYPES,
    ),
    intake_field(
        "education.schoolName", "美国学校名称", "SEVIS / 学生信息",
        placeholder="请与 I-20 一致", visa_types=F_VISA_TYPES,
    ),
    intake_field(
        "education.programName", "课程或专业名称", "SEVIS / 学生信息",
        placeholder="Course of Study", visa_types=F_VISA_TYPES,
    ),
    intake_field(
        "education.schoolStreet1", "学校地址 · 街道地址 1", "SEVIS / 学生信息",
        visa_types=F_VISA_TYPES, covered_by=["education.schoolAddress"],
    ),
    intake_field(
        "education.schoolStreet2", "学校地址 · 街道地址 2", "SEVIS / 学生信息",
        placeholder="没有时可留空", risk_level="low", visa_types=F_VISA_TYPES,
        required=False,
    ),
    intake_field("education.schoolCity", "学校地址 · 城市", "SEVIS / 学生信息", visa_types=F_VISA_TYPES),
    intake_field("education.schoolState", "学校地址 · 州", "SEVIS / 学生信息", visa_types=F_VISA_TYPES),
    intake_field(
        "education.schoolPostalCode", "学校地址 · 邮编", "SEVIS / 学生信息",
        risk_level="low", visa_types=F_VISA_TYPES,
    ),
    intake_field(
        "education.programStartDate", "项目开始日期", "SEVIS / 学生信息",
        input_type="date", placeholder="YYYY-MM-DD", visa_types=FJ_VISA_TYPES,
    ),
    intake_field(
        "education.programEndDate", "项目结束日期", "SEVIS / 学生信息",
        input_type="date", placeholder="YYYY-MM-DD", visa_types=FJ_VISA_TYPES,
    ),
    intake_field(
        "education.programNumber", "DS-2019 Program Number", "SEVIS / 学生信息",
        placeholder="请与 DS-2019 一致", risk_level="high", visa_types=J_VISA_TYPES,
    ),
    intake_field(
        "education.sponsorName", "项目 Sponsor 名称", "SEVIS / 学生信息",
        placeholder="请与 DS-2019 一致", visa_types=J_VISA_TYPES,
    ),
    intake_field(
        "education.programCategory", "J 项目类别", "SEVIS / 学生信息",
        placeholder="例如：Research Scholar / Intern", visa_types=J_VISA_TYPES,
    ),
]
