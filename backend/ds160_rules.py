#!/usr/bin/env python3
"""DS-160 questionnaire rules owned by the backend."""
from copy import deepcopy
from datetime import datetime, timezone
import re


YES_NO_CHOICES = [
    {"value": "yes", "label": "是 / Yes"},
    {"value": "no", "label": "否 / No"},
    {"value": "unknown", "label": "待客户确认"},
]

RULESET_VERSION = "ds160-bfj-2026-07-23-v11"

ALL_VISA_TYPES = ["b1b2", "f1", "f2", "j1", "j2"]
F_VISA_TYPES = ["f1", "f2"]
J_VISA_TYPES = ["j1", "j2"]
FJ_VISA_TYPES = ["f1", "f2", "j1", "j2"]


def detail(
    field_id, label, field_type="text", required=True, when=None,
    field_key=None, placeholder="", choices=None, hide_when=None,
):
    item = {
        "id": field_id,
        "label": label,
        "type": field_type,
        "required": required,
        "placeholder": placeholder,
    }
    if when:
        item["when"] = list(when)
    if field_key:
        item["fieldId"] = field_key
    if choices:
        item["choices"] = deepcopy(choices)
    if hide_when:
        item["hideWhen"] = deepcopy(hide_when)
    return item


def question(
    question_id,
    section,
    label,
    english_label="",
    *,
    answer_type="yes_no",
    choices=None,
    evidence=None,
    guidance="",
    sensitive=False,
    detail_fields=None,
    record_fields=None,
    record_label="记录",
    trigger_values=None,
    parent=None,
    parent_values=None,
    visa_types=None,
    min_records=1,
    client_optional=False,
):
    return {
        "id": question_id,
        "section": section,
        "label": label,
        "englishLabel": english_label,
        "answerType": answer_type,
        "choices": deepcopy(choices or (YES_NO_CHOICES if answer_type == "yes_no" else [])),
        "evidenceSources": list(evidence or []),
        "guidance": guidance,
        "sensitive": sensitive,
        "aiPolicy": "manual_only" if sensitive else "document_assist",
        "detailFields": deepcopy(detail_fields or []),
        "recordFields": deepcopy(record_fields or []),
        "recordLabel": record_label,
        "triggerValues": list(trigger_values or (["yes"] if answer_type == "yes_no" else [])),
        "parentQuestionId": parent,
        "parentValues": list(parent_values or ["yes"]),
        "visaTypes": list(visa_types or ALL_VISA_TYPES),
        "minRecords": max(0, int(1 if min_records is None else min_records)),
        "clientOptional": bool(client_optional),
    }


MARITAL_CHOICES = [
    {"value": "single", "label": "未婚 / Single"},
    {"value": "married", "label": "已婚 / Married"},
    {"value": "common_law", "label": "事实婚姻 / Common Law"},
    {"value": "civil_union", "label": "Civil Union / Domestic Partnership"},
    {"value": "divorced", "label": "离婚 / Divorced"},
    {"value": "widowed", "label": "丧偶 / Widowed"},
    {"value": "legally_separated", "label": "法律分居 / Legally Separated"},
    {"value": "other", "label": "其他 / Other"},
    {"value": "unknown", "label": "待客户确认"},
]

PAYER_CHOICES = [
    {"value": "self", "label": "本人 / Self"},
    {"value": "other_person", "label": "其他个人（父母、监护人或亲属等）/ Other Person"},
    {"value": "present_employer", "label": "当前雇主 / Present Employer"},
    {"value": "us_employer", "label": "美国雇主 / Employer in the U.S."},
    {"value": "other_organization", "label": "其他机构 / Organization"},
    {"value": "unknown", "label": "待客户确认"},
]

PAYER_RELATIONSHIP_CHOICES = [
    {"value": "PARENT", "label": "父母 / Parent"},
    {"value": "LEGAL GUARDIAN", "label": "法定监护人 / Legal Guardian"},
    {"value": "SPOUSE", "label": "配偶 / Spouse"},
    {"value": "OTHER RELATIVE", "label": "其他亲属 / Other Relative"},
    {"value": "FRIEND", "label": "朋友 / Friend"},
    {"value": "OTHER", "label": "其他 / Other"},
]

YES_NO_DETAIL_CHOICES = [
    {"value": "yes", "label": "是 / Yes"},
    {"value": "no", "label": "否 / No"},
]

STAY_UNIT_CHOICES = [
    {"value": "DAY", "label": "天 / Day(s)"},
    {"value": "WEEK", "label": "周 / Week(s)"},
    {"value": "MONTH", "label": "月 / Month(s)"},
    {"value": "YEAR", "label": "年 / Year(s)"},
]

COMPANION_RELATIONSHIP_CHOICES = [
    {"value": "PARENT", "label": "父母 / Parent"},
    {"value": "SPOUSE", "label": "配偶 / Spouse"},
    {"value": "CHILD", "label": "子女 / Child"},
    {"value": "OTHER RELATIVE", "label": "其他亲属 / Other Relative"},
    {"value": "FRIEND", "label": "朋友 / Friend"},
    {"value": "BUSINESS ASSOCIATE", "label": "业务往来人员 / Business Associate"},
    {"value": "OTHER", "label": "其他 / Other"},
]

OCCUPATION_CHOICES = [
    {"value": "agriculture", "label": "农业 / Agriculture"},
    {"value": "artist_performer", "label": "艺术家或表演者 / Artist or Performer"},
    {"value": "business", "label": "商业 / Business"},
    {"value": "communications", "label": "传媒 / Communications"},
    {"value": "computer_science", "label": "计算机 / Computer Science"},
    {"value": "culinary_food", "label": "餐饮 / Culinary or Food Services"},
    {"value": "education", "label": "教育 / Education"},
    {"value": "engineering", "label": "工程 / Engineering"},
    {"value": "government", "label": "政府 / Government"},
    {"value": "not_employed", "label": "待业 / Not Employed"},
    {"value": "homemaker", "label": "家庭主理 / Homemaker"},
    {"value": "legal", "label": "法律 / Legal Profession"},
    {"value": "medical_health", "label": "医疗健康 / Medical or Health"},
    {"value": "retired", "label": "退休 / Retired"},
    {"value": "military", "label": "军职 / Military"},
    {"value": "natural_science", "label": "自然科学 / Natural Science"},
    {"value": "physical_science", "label": "物理科学 / Physical Sciences"},
    {"value": "religious", "label": "宗教职业 / Religious Vocation"},
    {"value": "research", "label": "研究 / Research"},
    {"value": "social_science", "label": "社会科学 / Social Science"},
    {"value": "student", "label": "学生 / Student"},
    {"value": "other", "label": "其他 / Other"},
    {"value": "unknown", "label": "待客户确认"},
]

ACTIVE_OCCUPATION_VALUES = [
    choice["value"] for choice in OCCUPATION_CHOICES
    if choice["value"] not in {"not_employed", "homemaker", "retired", "other", "unknown"}
]

ACTIVE_NON_STUDENT_OCCUPATION_VALUES = [
    value for value in ACTIVE_OCCUPATION_VALUES if value != "student"
]

EDUCATION_LEVEL_CHOICES = [
    {"value": "secondary", "label": "初中 / 高中 / 中学阶段"},
    {"value": "vocational", "label": "中专 / 职业教育"},
    {"value": "college", "label": "大专 / 本科"},
    {"value": "postgraduate", "label": "硕士 / 博士"},
    {"value": "other", "label": "其他"},
]

SOCIAL_MEDIA_PLATFORM_CHOICES = [
    {"value": "ASK_FM", "label": "Ask.fm"},
    {"value": "DOUBAN", "label": "豆瓣 / Douban"},
    {"value": "FACEBOOK", "label": "Facebook"},
    {"value": "FLICKR", "label": "Flickr"},
    {"value": "GOOGLE_PLUS", "label": "Google+"},
    {"value": "INSTAGRAM", "label": "Instagram"},
    {"value": "LINKEDIN", "label": "LinkedIn"},
    {"value": "MYSPACE", "label": "Myspace"},
    {"value": "PINTEREST", "label": "Pinterest"},
    {"value": "QZONE", "label": "QQ 空间 / Qzone (QQ)"},
    {"value": "REDDIT", "label": "Reddit"},
    {"value": "SINA_WEIBO", "label": "新浪微博 / Sina Weibo"},
    {"value": "TENCENT_WEIBO", "label": "腾讯微博 / Tencent Weibo"},
    {"value": "TUMBLR", "label": "Tumblr"},
    {"value": "TWITTER", "label": "X / Twitter"},
    {"value": "TWOO", "label": "Twoo"},
    {"value": "VINE", "label": "Vine"},
    {"value": "VK", "label": "VKontakte (VK)"},
    {"value": "YOUKU", "label": "优酷 / Youku"},
    {"value": "YOUTUBE", "label": "YouTube"},
]


QUESTIONS = [
    question(
        "personal.other_names",
        "基础信息",
        "客户是否曾使用其他姓名？",
        "Have you ever used other names?",
        evidence=["旧护照", "学校或工作记录", "改名或婚姻文件"],
        guidance="包括婚前姓名、改名前姓名、宗教名、艺名、职业名及历史材料中的其他完整拼写。",
        record_label="曾用姓名",
        record_fields=[
            detail("surname", "曾用姓（英文）"),
            detail("givenNames", "曾用名（英文）"),
        ],
    ),
    question(
        "personal.telecode",
        "基础信息",
        "客户是否有中文姓名电码？",
        "Do you have a telecode that represents your name?",
        evidence=["中文姓名电码表", "客户确认"],
        guidance="Telecode 是非罗马字符对应的四位电码，不是拼音或身份证号码。",
        detail_fields=[
            detail("surnameTelecode", "姓氏电码"),
            detail("givenNamesTelecode", "名字电码"),
        ],
    ),
    question(
        "personal.marital_status",
        "基础信息",
        "客户当前婚姻状况是什么？",
        "What is your marital status?",
        answer_type="select",
        choices=MARITAL_CHOICES,
        evidence=["客户确认", "结婚证 / 离婚文件（如适用）"],
    ),
    question(
        "personal.current_spouse",
        "基础信息",
        "填写当前配偶或伴侣资料",
        answer_type="details",
        parent="personal.marital_status",
        parent_values=["married", "common_law", "civil_union"],
        evidence=["配偶护照或身份证明", "婚姻文件", "配偶地址"],
        detail_fields=[
            detail("surname", "配偶姓"), detail("givenNames", "配偶名"),
            detail("dateOfBirth", "出生日期", "date"), detail("nationality", "国籍"),
            detail("birthCity", "出生城市"), detail("birthCountry", "出生国家 / 地区"),
            detail("addressType", "地址类型", "text", False, placeholder="同家庭地址 / 同邮寄地址 / 其他"),
            detail("address", "配偶地址", "textarea", False),
        ],
    ),
    question(
        "personal.deceased_spouse",
        "基础信息",
        "填写已故配偶资料",
        answer_type="details",
        parent="personal.marital_status",
        parent_values=["widowed"],
        evidence=["已故配偶身份证明", "婚姻文件", "客户确认"],
        detail_fields=[
            detail("surname", "已故配偶姓"), detail("givenNames", "已故配偶名"),
            detail("dateOfBirth", "出生日期", "date"), detail("nationality", "国籍"),
            detail("birthCity", "出生城市"), detail("birthCountry", "出生国家 / 地区"),
            detail("lastKnownAddress", "最后已知地址或补充说明", "textarea", False),
        ],
    ),
    question(
        "personal.former_spouses",
        "基础信息",
        "填写每一位前配偶资料",
        answer_type="records",
        parent="personal.marital_status",
        parent_values=["divorced", "legally_separated"],
        evidence=["离婚文件", "前配偶基本资料"],
        record_label="前配偶",
        record_fields=[
            detail("surname", "姓"), detail("givenNames", "名"),
            detail("dateOfBirth", "出生日期", "date"), detail("nationality", "国籍"),
            detail("birthPlace", "出生地"), detail("marriageDate", "结婚日期", "date"),
            detail("marriageCountry", "结婚国家 / 地区"),
            detail("endDate", "婚姻结束日期", "date"), detail("endCountry", "婚姻结束国家 / 地区"),
            detail("endExplanation", "结束方式或说明", "textarea"),
        ],
    ),
    question(
        "personal.marital_other",
        "基础信息",
        "说明其他婚姻或伴侣关系",
        answer_type="details",
        parent="personal.marital_status",
        parent_values=["other"],
        detail_fields=[detail("explanation", "情况说明", "textarea")],
        evidence=["客户书面说明"],
    ),
    question(
        "personal.other_nationalities",
        "基础信息",
        "客户现在或过去是否拥有其他国籍？",
        "Do you hold or have you held any nationality other than the one indicated?",
        evidence=["现有及旧护照", "入籍或放弃国籍文件"],
        record_label="其他国籍",
        record_fields=[
            detail("country", "国家 / 地区"),
            detail("currentOrPast", "当前持有或过去持有"),
            detail("heldPassport", "是否持有或曾持有该国护照"),
            detail("passportNumber", "护照号码", "text", False),
        ],
    ),
    question(
        "personal.permanent_resident_other_country",
        "基础信息",
        "客户是否是其他国家或地区的永久居民？",
        "Are you a permanent resident of a country/region other than your country of nationality?",
        evidence=["永久居留证件"],
        record_label="永久居留资格",
        record_fields=[detail("country", "永久居留国家 / 地区")],
    ),
    question(
        "personal.has_ssn",
        "基础信息",
        "客户是否有美国 Social Security Number？",
        evidence=["Social Security Card", "客户确认"],
        detail_fields=[detail("number", "Social Security Number")],
    ),
    question(
        "personal.has_us_tax_id",
        "基础信息",
        "客户是否有美国纳税人识别号？",
        evidence=["美国税务文件", "客户确认"],
        detail_fields=[detail("number", "U.S. Taxpayer ID Number")],
    ),
    question(
        "travel.specific_plans",
        "旅行信息",
        "客户是否已经制定具体旅行计划？",
        "Have you made specific travel plans?",
        trigger_values=["yes", "no"],
        evidence=["机票或行程单", "酒店预订单", "客户确认"],
        guidance=(
            "选择“否”表示尚无确定航班或完整行程，但仍需填写预计抵达日期、"
            "预计停留时长和在美停留地址；只有选择“是”才会继续询问航班、离境日期和访问地点。"
        ),
        detail_fields=[
            detail("arrivalDate", "预计抵达日期 / Intended Date of Arrival", "date", True, field_key="travel.arrivalDate"),
            detail("stayLength", "预计停留数量 / Intended Length of Stay", placeholder="例如：10"),
            detail("stayUnit", "预计停留单位", "select", choices=STAY_UNIT_CHOICES),
            detail("arrivalFlight", "抵达航班", "text", True, when=["yes"], field_key="travel.arrivalFlight"),
            detail("arrivalCity", "抵达城市", "text", True, when=["yes"], field_key="travel.arrivalCity"),
            detail("departureDate", "离开美国日期", "date", True, when=["yes"], field_key="travel.departureDate"),
            detail("departureFlight", "离境航班", "text", True, when=["yes"], field_key="travel.departureFlight"),
            detail("departureCity", "离开城市", "text", True, when=["yes"], field_key="travel.departureCity"),
            detail("locations", "计划访问地点", "textarea", True, when=["yes"], field_key="travel.locations"),
        ],
    ),
    question(
        "travel.b_visit_purpose",
        "旅行信息",
        "B 类访问目的",
        answer_type="select",
        choices=[
            {"value": "b1", "label": "临时商务 / B1"},
            {"value": "b2_tourism", "label": "旅游、探亲访友 / B2"},
            {"value": "b2_medical", "label": "医疗访问 / B2"},
            {"value": "b1b2", "label": "商务与旅游 / B1/B2"},
            {"value": "unknown", "label": "待顾问确认"},
        ],
        trigger_values=["b1", "b2_tourism", "b2_medical", "b1b2"],
        visa_types=["b1b2"],
        evidence=["客户行程目的说明", "邀请函", "会议或医疗材料"],
        detail_fields=[
            detail("purposeDetail", "具体访问目的", "textarea"),
            detail("medicalProvider", "美国医院或医生", "text", True, when=["b2_medical"]),
            detail("medicalAddress", "医疗机构地址", "textarea", True, when=["b2_medical"]),
            detail("treatmentPeriod", "预计治疗时间", "text", True, when=["b2_medical"]),
            detail("estimatedCost", "预计费用与承担人", "textarea", True, when=["b2_medical"]),
        ],
    ),
    question(
        "travel.j1_category",
        "旅行信息",
        "J-1 交流访问项目类别",
        answer_type="select",
        choices=[
            {"value": "au_pair", "label": "Au Pair"},
            {"value": "camp_counselor", "label": "Camp Counselor"},
            {"value": "student", "label": "College and University Student"},
            {"value": "government_visitor", "label": "Government Visitor"},
            {"value": "intern", "label": "Intern"},
            {"value": "international_visitor", "label": "International Visitor"},
            {"value": "physician", "label": "Physician"},
            {"value": "professor", "label": "Professor"},
            {"value": "research_scholar", "label": "Research Scholar"},
            {"value": "short_term_scholar", "label": "Short-Term Scholar"},
            {"value": "specialist", "label": "Specialist"},
            {"value": "summer_work_travel", "label": "Summer Work Travel"},
            {"value": "teacher", "label": "Teacher"},
            {"value": "trainee", "label": "Trainee"},
            {"value": "unknown", "label": "待核对 DS-2019"},
        ],
        trigger_values=["intern", "trainee"],
        visa_types=["j1"],
        evidence=["DS-2019", "DS-7002（Intern / Trainee 如适用）"],
        detail_fields=[detail("ds7002Checked", "DS-7002 是否已核对及备注", "textarea")],
    ),
    question(
        "travel.payer",
        "旅行信息",
        "谁承担本次旅行费用？",
        "Person/Entity Paying for Your Trip",
        answer_type="select",
        choices=PAYER_CHOICES,
        trigger_values=["other_person", "present_employer", "us_employer", "other_organization"],
        evidence=["资金证明", "资助声明", "在职或邀请材料"],
        detail_fields=[
            detail("surname", "支付人姓", when=["other_person"]),
            detail("givenNames", "支付人名", when=["other_person"]),
            detail("phone", "电话", when=["other_person", "present_employer", "us_employer", "other_organization"]),
            detail("email", "Email", "email", False, when=["other_person"]),
            detail(
                "relationship", "与客户关系", "select", True,
                when=["other_person"], choices=PAYER_RELATIONSHIP_CHOICES,
            ),
            detail(
                "relationshipOther", "其他关系说明", "text", False,
                when=["other_person"],
            ),
            detail("organization", "公司或机构名称", when=["present_employer", "us_employer", "other_organization"]),
            detail("address", "公司或机构完整地址", "textarea", True, when=["present_employer", "us_employer", "other_organization"]),
        ],
    ),
    question(
        "travel.payer_address_same",
        "旅行信息",
        "付款人地址是否与客户家庭地址或通信地址相同？",
        parent="travel.payer",
        parent_values=["other_person"],
        trigger_values=["no"],
        evidence=["付款人地址", "客户确认"],
        detail_fields=[detail("address", "付款人完整地址", "textarea", True, when=["no"])],
    ),
    question(
        "companions.has_companions",
        "同行人",
        "客户是否有同行人？",
        "Are there other persons traveling with you?",
        evidence=["同行人护照首页", "家庭或团组行程"],
    ),
    question(
        "companions.is_group",
        "同行人",
        "客户是否作为团体或组织成员出行？",
        "Are you traveling as part of a group or organization?",
        parent="companions.has_companions",
        parent_values=["yes"],
        evidence=["团组名单", "组织出行说明"],
        detail_fields=[detail("groupName", "团体或组织名称", when=["yes"])],
    ),
    question(
        "companions.people",
        "同行人",
        "逐一填写同行人",
        answer_type="records",
        parent="companions.is_group",
        parent_values=["no"],
        evidence=["同行人护照首页", "客户确认"],
        record_label="同行人",
        record_fields=[
            detail("surname", "姓"), detail("givenNames", "名"),
            detail("relationship", "与申请人的关系", "select", choices=COMPANION_RELATIONSHIP_CHOICES),
        ],
    ),
    question(
        "us_history.visited",
        "以往赴美记录",
        "客户是否曾经去过美国？",
        "Have you ever been in the U.S.?",
        evidence=["旧护照出入境章", "I-94", "历史行程"],
        record_label="赴美记录",
        record_fields=[
            detail("arrivalDate", "到达日期", "date"), detail("stayLength", "停留时间"),
            detail("stayUnit", "时间单位", "select", choices=STAY_UNIT_CHOICES),
        ],
    ),
    question(
        "us_history.drivers_license",
        "以往赴美记录",
        "客户是否持有或曾持有美国驾驶执照？",
        parent="us_history.visited",
        parent_values=["yes"],
        evidence=["美国驾驶执照", "客户确认"],
        record_label="美国驾驶执照",
        record_fields=[detail("number", "驾照号码"), detail("state", "签发州")],
    ),
    question(
        "us_history.previous_visa",
        "以往赴美记录",
        "客户是否曾获得美国签证？",
        "Have you ever been issued a U.S. visa?",
        evidence=["过往美国签证页", "旧护照"],
        detail_fields=[
            detail("issueDate", "最近签发日期", "date", True, field_key="history.previousVisaIssueDate"),
            detail(
                "visaNumber", "签证号码", "text", True,
                field_key="history.previousVisaNumber",
                placeholder="不知道时填写 DO NOT KNOW",
            ),
            detail("visaClass", "签证类别", "text", False, field_key="history.previousVisaClass"),
            detail("sameClass", "本次是否申请同类签证", "select", choices=YES_NO_DETAIL_CHOICES),
            detail("sameLocation", "是否在同一国家或地点申请且该地为主要居住地", "select", choices=YES_NO_DETAIL_CHOICES),
            detail("tenPrinted", "是否采集过十指指纹", "select", choices=YES_NO_DETAIL_CHOICES),
        ],
    ),
    question(
        "us_history.visa_lost_stolen",
        "以往赴美记录",
        "最近一次美国签证是否遗失或被盗？",
        parent="us_history.previous_visa",
        parent_values=["yes"],
        evidence=["报案记录", "旧签证资料", "客户书面说明"],
        detail_fields=[detail("year", "遗失或被盗年份"), detail("explanation", "详细说明", "textarea")],
    ),
    question(
        "us_history.visa_cancelled",
        "以往赴美记录",
        "最近一次美国签证是否被取消或撤销？",
        parent="us_history.previous_visa",
        parent_values=["yes"],
        evidence=["签证页标注", "使领馆或移民机关文件", "客户书面说明"],
        detail_fields=[detail("explanation", "时间、地点、类别、处理机关和已知原因", "textarea")],
    ),
    question(
        "us_history.refusal_or_admission",
        "以往赴美记录",
        "客户是否曾被拒签、拒绝入境或撤回入境申请？",
        "Have you ever been refused a U.S. visa, refused admission, or withdrawn your application for admission?",
        sensitive=True,
        evidence=["拒签通知", "221(g) 文件", "入境处理文件", "客户书面说明"],
        guidance="不得根据护照是否盖章或后来是否获签推定答案。由顾问结合客户真实记录逐项确认。",
        record_label="拒签 / 入境事件",
        record_fields=[
            detail("date", "日期", "date"), detail("location", "使领馆或入境口岸"),
            detail("visaClass", "签证类别"), detail("legalSection", "法律条款（如已知）", "text", False),
            detail("outcome", "后续结果"), detail("explanation", "事实说明", "textarea"),
        ],
    ),
    question(
        "us_history.immigrant_petition",
        "以往赴美记录",
        "是否有人为客户提交过美国移民申请或 petition？",
        "Has anyone ever filed an immigrant petition on your behalf?",
        sensitive=True,
        evidence=["I-130 / I-140 等收件或批准文件", "客户书面说明"],
        record_label="移民申请",
        record_fields=[
            detail("petitioner", "提交人"), detail("relationship", "关系"),
            detail("petitionType", "申请类型"), detail("filingDate", "大致提交日期", "date", False),
            detail("status", "当前状态"), detail("explanation", "补充说明", "textarea", False),
        ],
    ),
    question(
        "contact.mailing_same_as_home",
        "地址 / 电话 / 社交媒体",
        "邮寄地址是否与家庭地址相同？",
        trigger_values=["no"],
        evidence=["客户地址证明", "客户确认"],
        detail_fields=[detail("mailingAddress", "完整邮寄地址", "textarea", True, when=["no"])],
    ),
    question(
        "contact.other_phones",
        "地址 / 电话 / 社交媒体",
        "过去五年是否使用过其他电话号码？",
        evidence=["客户确认", "历史联系方式"],
        record_label="其他电话号码",
        record_fields=[detail("phone", "电话号码")],
    ),
    question(
        "contact.other_emails",
        "地址 / 电话 / 社交媒体",
        "过去五年是否使用过其他邮箱？",
        evidence=["客户确认", "历史邮箱"],
        record_label="其他邮箱",
        record_fields=[detail("email", "邮箱", "email")],
    ),
    question(
        "contact.social_media",
        "地址 / 电话 / 社交媒体",
        "过去五年是否使用过页面列出的社交媒体？",
        evidence=["客户确认", "账号资料"],
        guidance="请选择过去五年实际使用过的平台并填写用户名或 Handle。只记录账号标识，不收集密码、私信、验证码或登录凭证。",
        record_label="社交媒体账号",
        record_fields=[
            detail("platform", "平台", choices=SOCIAL_MEDIA_PLATFORM_CHOICES),
            detail("handle", "用户名 / Handle"),
        ],
    ),
    question(
        "contact.other_platforms",
        "地址 / 电话 / 社交媒体",
        "是否还在其他平台建立或分享内容？",
        evidence=["客户确认"],
        record_label="其他平台账号",
        record_fields=[detail("platform", "平台名称或类型"), detail("handle", "账号标识")],
    ),
    question(
        "passport.type",
        "护照信息",
        "护照或旅行证件类型",
        answer_type="select",
        choices=[
            {"value": "regular", "label": "普通 / Regular"},
            {"value": "official", "label": "公务或官方 / Official"},
            {"value": "diplomatic", "label": "外交 / Diplomatic"},
            {"value": "laissez_passer", "label": "通行证 / Laissez-Passer"},
            {"value": "other", "label": "其他 / Other"},
            {"value": "unknown", "label": "待核对"},
        ],
        trigger_values=["other"],
        evidence=["护照资料页"],
        detail_fields=[detail("otherType", "其他证件类型说明")],
    ),
    question(
        "passport.has_book_number",
        "护照信息",
        "该护照是否有 Passport Book Number？",
        evidence=["护照资料页", "签发机关说明"],
        guidance="没有时在真实 DS-160 中选择 Does Not Apply，不要重复填写护照号码。",
        detail_fields=[detail("bookNumber", "Passport Book Number", field_key="passport.bookNumber")],
    ),
    question(
        "passport.lost_stolen",
        "护照信息",
        "客户是否曾遗失或被盗护照？",
        evidence=["报案记录", "旧护照资料", "补发记录", "客户说明"],
        record_label="遗失或被盗护照",
        record_fields=[
            detail("passportNumber", "护照号码", "text", False), detail("issuingCountry", "签发国家 / 地区"),
            detail("explanation", "时间、地点、报案、补发及找回情况", "textarea"),
        ],
    ),
    question(
        "us_contact.knows_person",
        "美国联系人",
        "客户能够提供哪种美国联系信息？",
        answer_type="select",
        choices=[
            {"value": "person", "label": "知道具体个人联系人"},
            {"value": "organization", "label": "不知道个人，但知道学校 / 公司 / 酒店 / 机构"},
            {"value": "unknown", "label": "个人和机构都不确定，请顾问根据材料核对"},
        ],
        trigger_values=["person", "organization"],
        evidence=["邀请函", "学校或项目材料", "酒店资料", "客户确认"],
        detail_fields=[
            detail("surname", "联系人姓", when=["person"], field_key="contact.surname"),
            detail("givenNames", "联系人名", when=["person"], field_key="contact.givenNames"),
            detail("organization", "机构 / 学校 / 公司 / 酒店名称", "text", False, when=["person"], field_key="contact.organizationName"),
            detail("relationship", "该联系人与你的关系", when=["person"]),
            detail("organization", "学校 / 公司 / 酒店 / 机构名称", when=["organization"], field_key="contact.organizationName"),
            detail("address", "美国联系地址", "textarea", True, when=["person", "organization"], field_key="contact.usAddress"),
            detail("phone", "美国联系电话", "text", True, when=["person", "organization"], field_key="contact.phone"),
            detail("email", "美国联系邮箱", "email", False, when=["person", "organization"], field_key="contact.usEmail"),
        ],
    ),
    question(
        "family.father_known",
        "家庭信息",
        "请填写父亲姓名和出生日期",
        "Father's Full Name and Date of Birth",
        answer_type="details",
        evidence=["客户确认", "户籍或出生证明"],
        guidance="不知道的单项可填写 DO NOT KNOW；父亲已故也仍需按实际所知填写。",
        detail_fields=[
            detail("surname", "父亲姓"), detail("givenNames", "父亲名"),
            detail("dateOfBirth", "出生日期；不知道可填写 D", "date"),
        ],
    ),
    question(
        "family.father_in_us",
        "家庭信息",
        "客户父亲是否在美国？",
        evidence=["客户确认", "父亲美国身份资料（如有）"],
        detail_fields=[detail("status", "在美身份", when=["yes"])],
    ),
    question(
        "family.mother_known",
        "家庭信息",
        "请填写母亲姓名和出生日期",
        "Mother's Full Name and Date of Birth",
        answer_type="details",
        evidence=["客户确认", "户籍或出生证明"],
        guidance="不知道的单项可填写 DO NOT KNOW；母亲已故也仍需按实际所知填写。",
        detail_fields=[
            detail("surname", "母亲姓"), detail("givenNames", "母亲名"),
            detail("dateOfBirth", "出生日期；不知道可填写 D", "date"),
        ],
    ),
    question(
        "family.mother_in_us",
        "家庭信息",
        "客户母亲是否在美国？",
        evidence=["客户确认", "母亲美国身份资料（如有）"],
        detail_fields=[detail("status", "在美身份", when=["yes"])],
    ),
    question(
        "family.immediate_relatives_us",
        "家庭信息",
        "除父母外，客户是否有直系亲属在美国？",
        evidence=["亲属护照或美国身份证明", "客户确认"],
        record_label="在美直系亲属",
        record_fields=[
            detail("surname", "姓"), detail("givenNames", "名"),
            detail("relationship", "关系"), detail("usStatus", "在美身份"),
        ],
    ),
    question(
        "family.other_relatives_us",
        "家庭信息",
        "客户是否有其他亲属在美国？",
        evidence=["客户确认"],
        detail_fields=[detail("notes", "亲属关系及必要说明", "textarea", False)],
    ),
    question(
        "work.primary_occupation",
        "工作 / 教育 / 培训",
        "客户当前主要职业或状态是什么？",
        "Primary Occupation",
        answer_type="select",
        choices=OCCUPATION_CHOICES,
        trigger_values=ACTIVE_OCCUPATION_VALUES + ["not_employed", "homemaker", "retired", "other"],
        evidence=["在职证明", "学校证明", "简历", "客户确认"],
        detail_fields=[
            detail("organization", "当前雇主 / 学校", "text", True, when=ACTIVE_OCCUPATION_VALUES, field_key="work.employerName"),
            detail("address", "完整地址", "textarea", True, when=ACTIVE_OCCUPATION_VALUES, field_key="work.employerAddress"),
            detail("phone", "电话", "text", True, when=ACTIVE_OCCUPATION_VALUES, field_key="work.employerPhone"),
            detail("startDate", "入职 / 入学日期", "date", True, when=ACTIVE_OCCUPATION_VALUES, field_key="work.startDate"),
            detail("jobTitle", "职位", "text", True, when=ACTIVE_NON_STUDENT_OCCUPATION_VALUES, field_key="work.title"),
            detail("schoolLevel", "当前学习阶段", "select", True, when=["student"], choices=EDUCATION_LEVEL_CHOICES),
            detail(
                "courseOfStudy", "专业 / 学习方向", "text", True,
                when=["student"], field_key="education.programName",
                hide_when={"field": "schoolLevel", "values": ["secondary"]},
            ),
            detail("monthlyIncome", "当地货币月收入（如适用）", "text", False, when=ACTIVE_OCCUPATION_VALUES, field_key="work.monthlyIncome"),
            detail("duties", "工作职责", "textarea", True, when=ACTIVE_NON_STUDENT_OCCUPATION_VALUES, field_key="work.duties"),
            detail("explanation", "当前情况或其他职业类别说明", "textarea", True, when=["not_employed", "homemaker", "retired", "other"]),
        ],
    ),
    question(
        "work.previously_employed",
        "工作 / 教育 / 培训",
        "客户以前是否受雇？",
        evidence=["简历", "历史在职证明", "客户确认"],
        record_label="前雇主（通常最近两个）",
        record_fields=[
            detail("employer", "雇主名称"), detail("address", "地址", "textarea"),
            detail("phone", "电话"), detail("title", "职位"), detail("supervisor", "主管姓名", "text", False),
            detail("startDate", "入职日期", "date"), detail("endDate", "离职日期", "date"),
            detail("duties", "工作职责", "textarea"),
        ],
    ),
    question(
        "work.education_secondary_or_above",
        "工作 / 教育 / 培训",
        "客户是否就读过中学及以上教育机构？",
        evidence=["毕业证 / 学位证", "成绩单", "简历"],
        record_label="教育经历",
        record_fields=[
            detail("level", "教育阶段", "select", choices=EDUCATION_LEVEL_CHOICES),
            detail("school", "学校名称"),
            detail("address", "学校街道地址（系统自动补全）", "textarea", False),
            detail("city", "学校所在城市（系统自动补全）", "text", False),
            detail("region", "学校所在省 / 州（系统自动补全）", "text", False),
            detail("postalCode", "学校邮编", "text", False),
            detail("country", "学校所在国家 / 地区（系统自动补全）", "text", False),
            detail(
                "course", "专业 / Course of Study", "text", True,
                hide_when={"field": "level", "values": ["secondary"]},
            ),
            detail("startDate", "入学日期", "date"),
            detail("endDate", "离校日期", "date"),
        ],
    ),
    question(
        "additional.clan_tribe",
        "补充经历",
        "客户是否属于氏族或部落？",
        evidence=["客户确认"],
        detail_fields=[detail("name", "氏族或部落名称")],
    ),
    question(
        "additional.languages",
        "补充经历",
        "客户使用过哪些语言？",
        answer_type="records",
        evidence=["客户确认", "简历"],
        record_label="语言",
        record_fields=[detail("language", "语言名称")],
    ),
    question(
        "additional.countries_visited",
        "补充经历",
        "过去五年是否去过其他国家或地区？",
        evidence=["护照出入境章", "历史行程", "客户确认"],
        record_label="过去五年访问国家 / 地区",
        record_fields=[detail("country", "国家 / 地区")],
    ),
    question(
        "additional.organizations",
        "补充经历",
        "客户是否属于或参与专业、社会、慈善组织？",
        evidence=["会员证明", "简历", "客户确认"],
        record_label="组织",
        record_fields=[detail("name", "组织名称"), detail("role", "角色或关系", "text", False)],
    ),
    question(
        "additional.specialized_skills",
        "补充经历",
        "客户是否具有枪械、爆炸物、核、生物或化学等特殊技能或训练？",
        sensitive=True,
        evidence=["培训记录", "简历", "客户书面说明"],
        detail_fields=[detail("explanation", "技能类别、训练机构、时间、用途和背景", "textarea")],
    ),
    question(
        "additional.military_service",
        "补充经历",
        "客户是否曾服兵役？",
        sensitive=True,
        evidence=["退伍证 / 服役记录", "客户书面说明"],
        record_label="服役记录",
        record_fields=[
            detail("country", "服役国家"), detail("branch", "军种"), detail("unit", "单位或分支"),
            detail("rank", "军衔 / 职位"), detail("specialty", "军事专业"),
            detail("startDate", "开始日期", "date"), detail("endDate", "结束日期", "date"),
        ],
    ),
    question(
        "additional.paramilitary",
        "补充经历",
        "客户是否参与过准军事、治安、叛乱或武装组织？",
        sensitive=True,
        evidence=["客户书面说明", "相关官方记录"],
        detail_fields=[detail("explanation", "组织、国家、时间、职责和活动内容", "textarea")],
    ),
]


QUESTIONS.extend([
    question(
        "application.previous_ds160",
        "申请信息",
        "客户以前是否提交过 DS-160？",
        evidence=["以往 DS-160 确认页", "客户确认"],
        guidance="不要在此收集 CEAC 找回问题答案、验证码、密码或登录信息。",
        detail_fields=[
            detail("confirmationNumber", "以往 DS-160 Confirmation Number", "text", False),
            detail("submissionDate", "大致提交日期", "date", False),
        ],
    ),
    question(
        "dependent.principal_applicant",
        "旅行信息",
        "请填写主申请人的资料",
        answer_type="details",
        visa_types=["f2", "j2"],
        evidence=["主申请人 I-20 / DS-2019", "主申请人护照", "主申请人 DS-160 确认页（如有）"],
        detail_fields=[
            detail("surname", "主申请人姓"),
            detail("givenNames", "主申请人名"),
            detail("relationship", "与主申请人的关系", choices=[
                {"value": "spouse", "label": "配偶"},
                {"value": "child", "label": "子女"},
            ]),
            detail("principalVisaClass", "主申请人签证类别", choices=[
                {"value": "f1", "label": "F-1"},
                {"value": "j1", "label": "J-1"},
            ]),
            detail("principalSevisId", "主申请人的 SEVIS ID"),
            detail("confirmationNumber", "主申请人的 DS-160 Confirmation Number", "text", False),
            detail("travelsTogether", "主申请人是否同行", choices=[
                {"value": "yes", "label": "是 / Yes"},
                {"value": "no", "label": "否 / No"},
            ]),
        ],
    ),
    question(
        "fj.additional_contacts",
        "F/J 补充联系人",
        "请提供两位居住国的额外联系人",
        answer_type="records",
        visa_types=FJ_VISA_TYPES,
        min_records=0,
        client_optional=True,
        evidence=["客户通讯录", "联系人确认"],
        guidance=(
            "如客户当前没有可提供的信息，可以先跳过，由顾问在进入 CEAC 前按当次页面要求处理。"
            "已填写时请勿使用直系亲属或其他亲属。"
        ),
        record_label="额外联系人",
        record_fields=[
            detail("surname", "姓"), detail("givenNames", "名"),
            detail("address", "完整地址", "textarea"), detail("country", "国家或地区"),
            detail("phone", "电话"), detail("email", "Email", "email"),
            detail("relationship", "与申请人的关系"),
        ],
    ),
    question(
        "j.intends_to_study",
        "SEVIS / 学生信息",
        "客户是否计划在美国学习？",
        "Do you intend to study in the U.S.?",
        visa_types=J_VISA_TYPES,
        evidence=["DS-2019", "学校或项目材料", "客户确认"],
        detail_fields=[
            detail("schoolName", "学校或机构名称", when=["yes"]),
            detail("course", "课程或专业", when=["yes"]),
            detail("schoolAddress", "学校完整地址", "textarea", True, when=["yes"]),
        ],
    ),
    question(
        "photo.prepared",
        "照片与协助填写",
        "客户是否已准备近期美国签证数码照片？",
        evidence=["数码照片文件", "内部收件记录"],
        detail_fields=[
            detail("takenDate", "拍摄日期", "date", False, when=["yes"]),
            detail("fileReference", "照片文件名或内部收件编号", "text", False, when=["yes"]),
        ],
    ),
])


SENSITIVE_QUESTIONS = [
    ("security.communicable_disease", "健康与背景", "客户是否涉及 DS-160 所列传染病问题？", "疾病名称、诊断时间、治疗和当前状态", ["医疗记录", "客户书面说明"]),
    ("security.mental_physical_harm", "健康与背景", "客户是否涉及与可能威胁本人或他人安全、福利的行为相关的身体或精神障碍？", "情况性质、有害行为、时间、治疗和当前状态", ["医疗记录", "客户书面说明"]),
    ("security.drug_abuse", "健康与背景", "客户是否涉及药物滥用或成瘾问题？", "涉及物质、时间、诊断或处理、当前状态", ["医疗记录", "客户书面说明"]),
    ("security.arrest_conviction", "犯罪背景", "客户是否曾因违法或犯罪被逮捕或定罪？", "逐案说明日期、地点、机关、罪名、法院、结果与刑罚", ["判决书", "警方或法院记录", "客户书面说明"]),
    ("security.controlled_substance", "犯罪背景", "客户是否涉及毒品或受控物质违法？", "物质、行为、时间地点、案件及最终结果", ["警方或法院记录", "客户书面说明"]),
    ("security.prostitution", "犯罪背景", "客户是否涉及卖淫或相关商业化活动问题？", "行为、时间、地点、角色及处理结果", ["客户书面说明", "官方记录"]),
    ("security.money_laundering", "犯罪背景", "客户是否涉及洗钱问题？", "行为、时间、地点、调查或案件结果", ["客户书面说明", "官方记录"]),
    ("security.trafficking_participation", "犯罪背景", "客户是否曾实施或共谋人口贩运？", "行为、角色、时间地点和处理结果", ["客户书面说明", "官方记录"]),
    ("security.trafficking_assistance", "犯罪背景", "客户是否曾明知而协助人口贩运相关人员？", "被协助人、关系、协助方式、时间地点和处理结果", ["客户书面说明", "官方记录"]),
    ("security.trafficking_benefit", "犯罪背景", "客户是否作为相关人员的配偶或子女，明知而从人口贩运活动中获益？", "相关人员、家庭关系、获益情况、时间地点和处理结果", ["客户书面说明", "官方记录"]),
    ("security.espionage_export", "国家安全与人权", "客户是否涉及间谍、破坏、非法技术出口或其他相关违法活动？", "行为、时间地点、组织和个人角色", ["客户书面说明", "官方记录"]),
    ("security.terrorist_activity", "国家安全与人权", "客户是否计划从事或曾经从事恐怖活动？", "活动、时间地点、组织和个人角色", ["客户书面说明", "官方记录"]),
    ("security.terrorist_support", "国家安全与人权", "客户是否曾为恐怖活动或相关人员提供资金、物质或其他支持？", "支持对象、方式、时间地点和个人角色", ["客户书面说明", "官方记录"]),
    ("security.terrorist_membership", "国家安全与人权", "客户是否是或曾是恐怖组织成员或代表？", "组织、身份、职责、时间地点和退出情况", ["客户书面说明", "官方记录"]),
    ("security.terrorist_family", "国家安全与人权", "客户是否涉及 DS-160 恐怖活动相关人员的家庭成员问题？", "相关人员、家庭关系、时间及其他事实", ["客户书面说明", "官方记录"]),
    ("security.genocide", "国家安全与人权", "客户是否涉及种族灭绝？", "国家、时间、组织、职务及参与程度", ["客户书面说明", "官方记录"]),
    ("security.torture", "国家安全与人权", "客户是否涉及酷刑？", "事件、时间地点、组织和个人角色", ["客户书面说明", "官方记录"]),
    ("security.extrajudicial_violence", "国家安全与人权", "客户是否涉及法外处决、政治杀戮或其他暴力？", "事件、职务、命令或参与情况、时间地点", ["客户书面说明", "官方记录"]),
    ("security.child_soldiers", "国家安全与人权", "客户是否涉及招募或使用儿童兵？", "组织、角色、时间地点", ["客户书面说明", "官方记录"]),
    ("security.religious_freedom", "国家安全与人权", "客户是否涉及严重侵犯宗教自由？", "政府职务、行为、时间地点", ["客户书面说明", "官方记录"]),
    ("security.forced_abortion_sterilization", "国家安全与人权", "客户是否涉及强制堕胎或强制绝育？", "实际行为、时间地点和个人角色", ["客户书面说明", "官方记录"]),
    ("security.forced_organ_transplant", "国家安全与人权", "客户是否涉及强制器官或人体组织移植？", "实际行为、时间地点和个人角色", ["客户书面说明", "官方记录"]),
    ("immigration.fraud_misrepresentation", "移民记录", "客户是否曾通过欺诈或虚假陈述获取美国移民利益？", "日期、申请或入境事件、信息、机构和结果", ["移民机关文件", "客户书面说明"]),
    ("immigration.removal", "移民记录", "客户是否曾被递解、遣返或移除？", "日期、地点、机构、程序、结果和禁入期", ["递解或移除文件", "客户书面说明"]),
    ("immigration.status_violation", "移民记录", "客户是否曾非法停留、未经许可工作或违反美国身份条件？", "入境与身份日期、I-94、违规期间、离境和处理情况", ["I-94", "USCIS / 法庭文件", "客户书面说明"]),
    ("immigration.assisted_illegal_entry", "移民记录", "客户是否曾协助他人非法进入美国？", "被协助人、关系、时间地点、方式和案件", ["客户书面说明", "官方记录"]),
    ("inadmissibility.child_custody", "其他背景问题", "客户是否涉及扣留美国公民儿童问题？", "儿童、监护权裁决、时间、国家和当前状态", ["监护权文件", "客户书面说明"]),
    ("inadmissibility.unlawful_voting", "其他背景问题", "客户是否曾在违反法律的情况下在美国投票？", "日期、地点、选举类型和处理", ["客户书面说明", "官方记录"]),
    ("inadmissibility.renounced_citizenship_tax", "其他背景问题", "客户是否为逃避美国税务而放弃美国国籍？", "放弃日期、税务背景和相关决定", ["国籍及税务文件", "客户书面说明"]),
]

for sensitive_id, section, label, explanation_label, evidence_sources in SENSITIVE_QUESTIONS:
    QUESTIONS.append(question(
        sensitive_id,
        section,
        label,
        sensitive=True,
        evidence=evidence_sources,
        guidance="系统不判断该题答案，也不会默认选择 No。请顾问按 CEAC 当次英文原题和客户真实事实逐项确认。",
        detail_fields=[detail("explanation", explanation_label, "textarea")],
    ))

QUESTIONS.extend([
    question(
        "inadmissibility.f1_public_school",
        "其他背景问题",
        "客户是否涉及 F 身份就读美国公立学校或未偿还依法应付费用的问题？",
        sensitive=True,
        visa_types=F_VISA_TYPES,
        evidence=["学校记录", "缴费记录", "身份文件", "客户书面说明"],
        detail_fields=[detail("explanation", "学校、日期、当时身份、费用和当前处理状态", "textarea")],
    ),
    question(
        "photo.upload_result",
        "照片与协助填写",
        "DS-160 照片上传结果",
        answer_type="select",
        choices=[
            {"value": "success", "label": "确认页显示照片 / 成功"},
            {"value": "failed", "label": "确认页显示 X / 失败"},
            {"value": "not_tested", "label": "尚未测试"},
        ],
        trigger_values=["failed"],
        evidence=["照片上传结果", "确认页"],
        detail_fields=[detail("followUp", "申请地纸质照片要求与跟进记录", "textarea")],
    ),
    question(
        "preparer.assisted",
        "照片与协助填写",
        "是否有人协助客户填写本次 DS-160？",
        "Did anyone assist you in filling out this application?",
        evidence=["机构内部经办记录", "客户确认"],
        guidance="中介、文案老师、顾问、翻译或家属参与填写时，应按页面要求识别协助人。",
        detail_fields=[
            detail("surname", "协助人姓"), detail("givenNames", "协助人名"),
            detail("organization", "机构名称", "text", False),
            detail("address", "地址", "textarea"), detail("relationship", "与客户关系或协助情况"),
        ],
    ),
])


SECTION_ORDER = [
    "申请信息", "基础信息", "旅行信息", "同行人", "以往赴美记录", "地址 / 电话 / 社交媒体",
    "护照信息", "美国联系人", "家庭信息", "工作 / 教育 / 培训", "补充经历",
    "F/J 补充联系人", "SEVIS / 学生信息",
    "健康与背景", "犯罪背景", "国家安全与人权", "移民记录", "其他背景问题",
    "照片与协助填写",
]


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def visa_id(visa_type):
    normalized = str(visa_type or "").strip().upper()
    if normalized.startswith("B"):
        return "b1b2"
    if normalized.startswith("F2") or normalized.startswith("F-2"):
        return "f2"
    if normalized.startswith("J2") or normalized.startswith("J-2"):
        return "j2"
    if normalized.startswith("J"):
        return "j1"
    return "f1"


def field_visible(field, answer):
    allowed = field.get("when")
    return not allowed or answer in allowed


def dependent_field_visible(field, values):
    condition = field.get("hideWhen") or {}
    source_field = condition.get("field")
    hidden_values = {str(value) for value in (condition.get("values") or [])}
    if not source_field or not hidden_values:
        return True
    return str((values or {}).get(source_field) or "") not in hidden_values


def question_visible(item, by_id):
    parent_id = item.get("parentQuestionId")
    if not parent_id:
        return True
    parent = by_id.get(parent_id)
    if not parent or not parent.get("visible", True):
        return False
    return parent.get("answer") in (item.get("parentValues") or ["yes"])


def migrate_travel_details(details, extracted):
    """Keep existing cases usable after splitting CEAC travel controls."""
    migrated = deepcopy(details or {})
    legacy_duration = str(
        migrated.get("stayDuration")
        or (extracted.get("travel.stayDuration") or {}).get("value")
        or ""
    ).strip()
    duration_match = re.fullmatch(
        r"(\d+(?:\.\d+)?)\s*(DAY|DAYS|WEEK|WEEKS|MONTH|MONTHS|YEAR|YEARS)",
        legacy_duration,
        flags=re.IGNORECASE,
    )
    if duration_match:
        migrated.setdefault("stayLength", duration_match.group(1))
        migrated.setdefault("stayUnit", duration_match.group(2).upper().rstrip("S"))

    legacy_address = str(
        migrated.get("usAddress")
        or (extracted.get("contact.usAddress") or {}).get("value")
        or ""
    ).strip()
    if legacy_address and not migrated.get("usStreet1"):
        parts = [part.strip() for part in legacy_address.split(",") if part.strip()]
        if len(parts) >= 3:
            region = re.fullmatch(
                r"([A-Za-z .'-]+?)(?:\s+(\d{5}(?:-\d{4})?))?", parts[-1]
            )
            if region:
                address_parts = {
                    "usStreet1": ", ".join(parts[:-2]),
                    "usCity": parts[-2],
                    "usState": region.group(1).strip(),
                }
                if region.group(2):
                    address_parts["usPostalCode"] = region.group(2)
                migrated.update(address_parts)
            else:
                migrated["usStreet1"] = legacy_address
        else:
            migrated["usStreet1"] = legacy_address
    return migrated


def infer_education_level(school_name):
    value = str(school_name or "").strip().lower()
    if re.search(r"小学|初中|高中|中学|middle school|high school|secondary school", value):
        return "secondary"
    if re.search(r"中专|职校|职业|技校|vocational", value):
        return "vocational"
    if re.search(r"研究生|研究院|graduate|postgraduate|博士|硕士", value):
        return "postgraduate"
    if re.search(r"大学|学院|大专|university|college", value):
        return "college"
    return ""


def student_detail_suggestions(extracted):
    def extracted_value(*field_ids):
        for field_id in field_ids:
            value = str((extracted.get(field_id) or {}).get("value") or "").strip()
            if value:
                return value
        return ""

    school_name = extracted_value("education.schoolName", "work.employerName")
    school_address = extracted_value("education.schoolAddress", "work.employerAddress")
    if not school_address:
        school_address = ", ".join(
            value for value in (
                extracted_value("education.schoolStreet1"),
                extracted_value("education.schoolStreet2"),
                extracted_value("education.schoolCity"),
                extracted_value("education.schoolState"),
                extracted_value("education.schoolPostalCode"),
            ) if value
        )
    level = infer_education_level(school_name)
    course = extracted_value("education.programName", "work.title")
    if level == "secondary":
        course = ""
    return {
        "organization": school_name,
        "address": school_address,
        "phone": extracted_value("education.schoolPhone", "work.employerPhone"),
        "startDate": extracted_value("education.currentSchoolStartDate", "work.startDate"),
        "schoolLevel": level,
        "courseOfStudy": course,
    }


def build_questionnaire(visa_type, existing=None, extracted_fields=None):
    selected_visa = visa_id(visa_type)
    previous = {item.get("id"): item for item in (existing or [])}
    extracted = {item.get("id"): item for item in (extracted_fields or []) if item.get("value")}
    output = []

    for definition in QUESTIONS:
        if selected_visa not in definition.get("visaTypes", []):
            continue
        item = deepcopy(definition)
        saved = previous.get(item["id"], {})
        saved_answer = saved.get("answer", "")
        if item["id"] == "us_contact.knows_person":
            saved_answer = {"yes": "person", "no": "organization"}.get(saved_answer, saved_answer)
        item.update({
            "answer": saved_answer,
            "details": deepcopy(saved.get("details") or {}),
            "records": deepcopy(saved.get("records") or []),
            "clientResponse": saved.get("clientResponse") or "",
            "originalClientResponse": saved.get("originalClientResponse") or "",
            "originalDetails": deepcopy(saved.get("originalDetails") or {}),
            "originalRecords": deepcopy(saved.get("originalRecords") or []),
            "translationProviders": deepcopy(saved.get("translationProviders") or {}),
            "clientSubmitted": bool(saved.get("clientSubmitted")),
            "confirmedByUser": bool(saved.get("confirmedByUser")),
            "source": saved.get("source") or "客户确认",
            "updatedAt": saved.get("updatedAt") or "",
            "autoDetermined": bool(saved.get("autoDetermined")),
            "answerConfidence": saved.get("answerConfidence"),
            "answerEvidence": saved.get("answerEvidence") or "",
        })
        if item["id"] == "travel.specific_plans":
            item["details"] = migrate_travel_details(item["details"], extracted)
        if item["id"] == "work.primary_occupation":
            legacy_title = str(item["details"].pop("titleOrMajor", "") or "").strip()
            if legacy_title:
                target = "courseOfStudy" if saved_answer == "student" else "jobTitle"
                item["details"].setdefault(target, legacy_title)
            suggestions = {
                key: value for key, value in student_detail_suggestions(extracted).items() if value
            }
            item["answerSuggestions"] = {"student": suggestions}
            if saved_answer == "student":
                for key, value in suggestions.items():
                    item["details"].setdefault(key, value)
                if suggestions:
                    item["source"] = "材料预填：学校或录取材料"
            if item["details"].get("schoolLevel") == "secondary":
                item["details"].pop("courseOfStudy", None)
        if item["id"] == "work.education_secondary_or_above":
            for record in item["records"]:
                if record.get("level") == "secondary":
                    record.pop("course", None)
        for detail_field in item.get("detailFields") or []:
            field_key = detail_field.get("fieldId")
            if not field_key or item["details"].get(detail_field["id"]):
                continue
            source_field = extracted.get(field_key)
            if source_field:
                item["details"][detail_field["id"]] = source_field.get("value")
                item["source"] = f"材料预填：{source_field.get('sourceDocument') or '上传材料'}"
        if item["id"] == "travel.specific_plans" and not item["details"].get("stayLength"):
            try:
                arrival = datetime.fromisoformat(str(item["details"].get("arrivalDate") or ""))
                departure = datetime.fromisoformat(str(item["details"].get("departureDate") or ""))
                day_count = (departure - arrival).days
                if day_count > 0:
                    item["details"]["stayLength"] = str(day_count)
                    item["details"]["stayUnit"] = "DAY"
            except ValueError:
                pass
        output.append(item)

    by_id = {item["id"]: item for item in output}
    for _ in range(3):
        for item in output:
            item["visible"] = question_visible(item, by_id)

    for item in output:
        if not item["visible"]:
            item["answer"] = ""
            item["details"] = {}
            item["records"] = []
            item["clientResponse"] = ""
            item["originalClientResponse"] = ""
            item["originalDetails"] = {}
            item["originalRecords"] = []
            item["translationProviders"] = {}
            item["clientSubmitted"] = False
            item["confirmedByUser"] = False
            item["autoDetermined"] = False
            item["answerConfidence"] = None
            item["answerEvidence"] = ""
        elif item.get("answerType") not in {"details", "records"}:
            trigger_values = item.get("triggerValues") or []
            if item.get("answer") in {"", "unknown"}:
                pass
            elif trigger_values and item.get("answer") not in trigger_values:
                item["details"] = {}
                item["records"] = []
            else:
                active_ids = {field["id"] for field in active_detail_fields(item)}
                if item.get("id") == "travel.specific_plans":
                    # Preserve addresses collected by earlier schema versions without
                    # exposing them under the travel-plan Yes/No question again.
                    active_ids.update({
                        "usStreet1", "usStreet2", "usCity", "usState", "usPostalCode",
                    })
                item["details"] = {
                    key: value for key, value in item.get("details", {}).items()
                    if key in active_ids
                }
        item["status"] = question_status(item)
    section_position = {section: index for index, section in enumerate(SECTION_ORDER)}
    output.sort(key=lambda item: section_position.get(item.get("section"), len(section_position)))
    return output


def infer_questionnaire_answers(questionnaire, documents=None, extracted_fields=None):
    """Extract only explicit answers and safe positive branches from uploaded evidence."""
    output = deepcopy(questionnaire or [])
    documents = documents or []
    fields = {
        item.get("id"): item for item in (extracted_fields or [])
        if str(item.get("value") or "").strip()
    }
    issues = []

    deterministic_yes = {
        "travel.specific_plans": [
            "travel.arrivalFlight", "travel.departureFlight",
        ],
        "history.previous_visa": [
            "history.previousVisaNumber", "history.previousVisaIssueDate",
            "history.previousVisaClass",
        ],
        "passport.has_book_number": ["passport.bookNumber"],
    }

    contact_question = next(
        (item for item in output if item.get("id") == "us_contact.knows_person"), None
    )
    if contact_question and not contact_question.get("confirmedByUser"):
        if "contact.surname" in fields or "contact.givenNames" in fields:
            contact_question.update({
                "answer": "person",
                "source": "材料字段判断：美国个人联系人",
                "autoDetermined": True,
                "answerConfidence": 0.94,
                "answerEvidence": "材料已识别联系人姓名",
                "updatedAt": now_iso(),
            })
        elif "contact.organizationName" in fields:
            contact_question.update({
                "answer": "organization",
                "source": "材料字段判断：美国联系机构",
                "autoDetermined": True,
                "answerConfidence": 0.94,
                "answerEvidence": "材料已识别学校、公司、酒店或机构名称",
                "updatedAt": now_iso(),
            })

    for item in output:
        if item.get("answerType") != "yes_no" or item.get("visible") is False:
            continue
        if item.get("confirmedByUser") or (
            item.get("answer") in {"yes", "no"}
            and not item.get("autoDetermined")
            and str(item.get("source") or "").startswith("客户")
        ):
            continue

        explicit_answers = []
        for document in documents:
            found = explicit_answer_from_text(item, document.get("text") or "")
            if found:
                explicit_answers.append({
                    "answer": found["answer"],
                    "evidence": found["evidence"],
                    "fileName": document.get("fileName") or "上传材料",
                })

        distinct = {answer["answer"] for answer in explicit_answers}
        if len(distinct) > 1:
            item["answer"] = ""
            item["autoDetermined"] = False
            item["answerConfidence"] = None
            item["answerEvidence"] = ""
            issues.append({
                "id": f"ocr.answer_conflict.{item.get('id')}",
                "type": "conflict",
                "severity": "high" if item.get("sensitive") else "medium",
                "category": "跨材料冲突",
                "message": f"“{item.get('label')}”在不同材料中出现相反答案，请核对原件。",
                "requiresUserResolution": True,
                "resolved": False,
            })
            continue
        if explicit_answers:
            evidence = explicit_answers[0]
            item.update({
                "answer": evidence["answer"],
                "source": f"材料明确回答：{evidence['fileName']}",
                "autoDetermined": True,
                "answerConfidence": 0.98,
                "answerEvidence": evidence["evidence"],
                "updatedAt": now_iso(),
            })
            continue

        evidence_fields = [field_id for field_id in deterministic_yes.get(item.get("id"), []) if field_id in fields]
        if evidence_fields:
            source_field = fields[evidence_fields[0]]
            item.update({
                "answer": "yes",
                "source": f"材料字段判断：{source_field.get('sourceDocument') or '上传材料'}",
                "autoDetermined": True,
                "answerConfidence": 0.94,
                "answerEvidence": "；".join(
                    f"{fields[field_id].get('label') or field_id}：{fields[field_id].get('value')}"
                    for field_id in evidence_fields
                )[:500],
                "updatedAt": now_iso(),
            })

    return output, issues


def explicit_answer_from_text(question_item, text):
    normalized_text = normalize_answer_text(text)
    if not normalized_text:
        return None
    labels = [question_item.get("englishLabel"), question_item.get("label")]
    for raw_label in labels:
        label = normalize_answer_text(raw_label)
        if len(label) < 6:
            continue
        position = normalized_text.find(label)
        if position < 0:
            continue
        context = normalized_text[position: position + len(label) + 320]
        answer = explicit_marker_value(context)
        if answer:
            return {"answer": answer, "evidence": context[:300]}
    return None


def normalize_answer_text(value):
    text = str(value or "").replace("\r", "\n")
    text = re.sub(r"\s+", " ", text)
    return text.strip().lower()


def explicit_marker_value(context):
    marker = r"(?:\[\s*[x✓✔]\s*\]|☒|☑|✓|✔)"
    answer_patterns = [
        rf"{marker}\s*(yes|no|是|否)(?=\s|[,.，。;；/]|$)",
        rf"(yes|no|是|否)\s*{marker}",
        r"(?:answer|response|selected|答案|选择|回答)\s*[:：-]?\s*(yes|no|是|否)(?=\s|[,.，。;；/]|$)",
    ]
    for pattern in answer_patterns:
        match = re.search(pattern, context, flags=re.IGNORECASE)
        if match:
            value = next((group for group in match.groups() if group), "").lower()
            return "yes" if value in {"yes", "是"} else "no"
    return ""


def active_detail_fields(item):
    answer = item.get("answer")
    details = item.get("details") or {}
    if item.get("answerType") == "details":
        return [
            field for field in (item.get("detailFields") or [])
            if dependent_field_visible(field, details)
        ]
    if item.get("triggerValues") and answer not in item["triggerValues"]:
        return []
    return [
        field for field in (item.get("detailFields") or [])
        if field_visible(field, answer) and dependent_field_visible(field, details)
    ]


def active_record_fields(item, record):
    return [
        field for field in (item.get("recordFields") or [])
        if dependent_field_visible(field, record or {})
    ]


def records_required(item):
    if not item.get("recordFields"):
        return False
    if item.get("answerType") == "records":
        return True
    return item.get("answer") in (item.get("triggerValues") or ["yes"])


def details_complete(item):
    details = item.get("details") or {}
    for field in active_detail_fields(item):
        if field.get("required") and not str(details.get(field["id"]) or "").strip():
            return False
    if records_required(item):
        records = item.get("records") or []
        if len(records) < int(item.get("minRecords", 1)):
            return False
        for record in records:
            for field in active_record_fields(item, record):
                if field.get("required") and not str(record.get(field["id"]) or "").strip():
                    return False
    return True


def question_status(item):
    if not item.get("visible", True):
        return "不适用"
    answer_type = item.get("answerType")
    if answer_type == "records":
        if not details_complete(item):
            return "客户已补充" if item.get("clientResponse") else "信息待补充"
    elif answer_type == "details":
        if not details_complete(item):
            return "客户已补充" if item.get("clientResponse") else "信息待补充"
    elif not item.get("answer") or item.get("answer") == "unknown":
        return "待客户确认"
    elif not details_complete(item):
        return "客户已补充" if item.get("clientResponse") else "信息待补充"
    if item.get("sensitive") and not item.get("confirmedByUser"):
        return "需顾问判断"
    return "已核查" if item.get("confirmedByUser") else "已回答"


def questionnaire_issues(questionnaire, existing_issues=None):
    existing = {item.get("id"): item for item in (existing_issues or [])}
    issues = []
    visible = [item for item in questionnaire if item.get("visible", True)]
    sensitive = [item for item in visible if item.get("sensitive")]
    unanswered = [item for item in sensitive if not item.get("answer") or item.get("answer") == "unknown"]
    unconfirmed = [item for item in sensitive if item.get("answer") not in {"", "unknown"} and not item.get("confirmedByUser")]

    if unanswered:
        issues.append(make_issue(
            "branch.sensitive.unanswered",
            "sensitive",
            "high",
            "安全与背景问题",
            f"仍有 {len(unanswered)} 项敏感历史问题等待客户逐题回答。系统不会默认选择 No。",
            True,
            existing,
        ))
    if unconfirmed:
        issues.append(make_issue(
            "branch.sensitive.unconfirmed",
            "sensitive",
            "high",
            "安全与背景问题",
            f"已有 {len(unconfirmed)} 项敏感问题录入答案，但尚未由顾问逐项核查确认。",
            True,
            existing,
        ))

    for item in sensitive:
        if item.get("answer") != "yes":
            continue
        issues.append(make_issue(
            f"branch.yes.{item['id']}",
            "sensitive",
            "high",
            item.get("section") or "安全与背景问题",
            f"“{item['label']}”回答为 Yes，请核查事实说明及相关材料。",
            True,
            existing,
        ))

    incomplete = [item for item in visible if not item.get("sensitive") and item.get("status") == "信息待补充"]
    for item in incomplete:
        issues.append(make_issue(
            f"branch.missing.{item['id']}",
            "missing",
            "medium",
            item.get("section") or "缺失信息",
            f"“{item['label']}”已触发附加字段，但资料尚未填写完整。",
            True,
            existing,
        ))
    return issues


def make_issue(issue_id, issue_type, severity, category, message, requires_resolution, existing):
    previous = existing.get(issue_id, {})
    return {
        "id": issue_id,
        "type": issue_type,
        "severity": severity,
        "category": category,
        "message": message,
        "requiresUserResolution": requires_resolution,
        "resolved": bool(previous.get("resolved")),
    }


def answer_label(item):
    answer = item.get("answer")
    for choice in item.get("choices") or []:
        if choice.get("value") == answer:
            return choice.get("label") or answer
    if item.get("answerType") == "records":
        return f"{len(item.get('records') or [])} 条记录"
    if item.get("answerType") == "details":
        return "已填写" if details_complete(item) else "待补充"
    return str(answer or "待确认")


def questionnaire_report_lines(questionnaire):
    lines = []
    for item in questionnaire:
        if not item.get("visible", True):
            continue
        lines.append(f"{item.get('section')} · {item.get('label')}：{answer_label(item)}（{item.get('status')}）")
    return lines


def sync_questionnaire_fields(existing_fields, questionnaire):
    existing = {field.get("id"): deepcopy(field) for field in (existing_fields or [])}
    managed_ids = {
        field.get("fieldId")
        for definition in QUESTIONS
        for field in (definition.get("detailFields") or [])
        if field.get("fieldId")
    }
    active_values = {}
    for item in questionnaire or []:
        if not item.get("visible", True) or item.get("sensitive"):
            continue
        if not str(item.get("source") or "").startswith("客户"):
            continue
        details = item.get("details") or {}
        for field in active_detail_fields(item):
            field_id = field.get("fieldId")
            value = str(details.get(field.get("id")) or "").strip()
            if field_id and value:
                active_values[field_id] = (value, field, item)
        if item.get("id") == "travel.specific_plans":
            stay_length = str(details.get("stayLength") or "").strip()
            stay_unit = str(details.get("stayUnit") or "").strip()
            if stay_length and stay_unit:
                active_values["travel.stayDuration"] = (
                    f"{stay_length} {stay_unit}",
                    {"label": "预计停留时间"}, item,
                )
                managed_ids.add("travel.stayDuration")
            address = ", ".join(
                str(details.get(key) or "").strip()
                for key in ("usStreet1", "usStreet2", "usCity", "usState", "usPostalCode")
                if str(details.get(key) or "").strip()
            )
            if address:
                active_values["contact.usAddress"] = (
                    address, {"label": "美国停留地址"}, item,
                )
                managed_ids.add("contact.usAddress")

    for field_id in managed_ids:
        previous = existing.get(field_id)
        if field_id not in active_values:
            if previous and previous.get("extractionMethod") == "questionnaire":
                existing.pop(field_id, None)
            continue
        value, definition, item = active_values[field_id]
        if (
            previous
            and previous.get("value")
            and previous.get("extractionMethod") in {
                "consultant_text", "consultant_text_semantic",
            }
            and str(item.get("source") or "").startswith("客户原始问答")
        ):
            continue
        risk_level = "high" if field_id.startswith(("passport.", "education.sevis", "history.")) else "medium"
        existing[field_id] = {
            **(previous or {}),
            "id": field_id,
            "label": definition.get("label") or field_id,
            "section": section_for_field(field_id, item.get("section")),
            "value": value,
            "sourceDocument": "条件问答",
            "sourceDocumentId": None,
            "sourcePage": None,
            "evidence": item.get("label") or "客户确认",
            "confidence": 1,
            "riskLevel": risk_level,
            "requiresUserConfirmation": True,
            "confirmed": False,
            "editedByUser": True,
            "extractionMethod": "questionnaire",
        }
    return list(existing.values())


def section_for_field(field_id, fallback):
    prefixes = {
        "personal.": "基础信息",
        "passport.": "护照信息",
        "travel.": "旅行信息",
        "contact.": "美国联系人",
        "education.": "SEVIS / 学生信息",
        "work.": "工作 / 教育 / 培训",
        "history.": "以往赴美记录",
    }
    for prefix, section in prefixes.items():
        if field_id.startswith(prefix):
            return section
    return fallback or "其他信息"
