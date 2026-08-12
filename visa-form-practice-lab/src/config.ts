import type { BilingualText, FieldConfig, FieldOption, StepConfig } from "./types.ts";

const b = (en: string, zh: string): BilingualText => ({ en, zh });
const option = (value: string, en: string, zh: string): FieldOption => ({ value, label: b(en, zh) });

const yesNoHint = b("Choose an answer for this practice session. Nothing is preselected.", "请为本次练习主动选择，系统不会默认勾选。 ");
const countryOptions = [
  option("EXAMPLELAND", "Exampleland (Fictional)", "示例国（虚构）"),
  option("CHINA", "China", "中国"),
  option("CANADA", "Canada", "加拿大"),
  option("JAPAN", "Japan", "日本"),
  option("SINGAPORE", "Singapore", "新加坡"),
  option("UNITED_KINGDOM", "United Kingdom", "英国"),
  option("OTHER", "Other / Not listed", "其他 / 未列出")
];

const relationshipOptions = [
  option("PARENT", "Parent", "父母"),
  option("SPOUSE", "Spouse or partner", "配偶或伴侣"),
  option("CHILD", "Child", "子女"),
  option("RELATIVE", "Other relative", "其他亲属"),
  option("FRIEND", "Friend", "朋友"),
  option("COLLEAGUE", "Colleague", "同事"),
  option("OTHER", "Other", "其他")
];

export const STEPS: StepConfig[] = [
  {
    id: "welcome",
    kind: "welcome",
    title: b("Welcome to your practice session", "欢迎进入本地练习"),
    shortTitle: b("Welcome", "开始"),
    description: b("Review the ground rules before working through the long-form practice flow.", "在进入长表单前，再次确认练习规则与本地存储说明。"),
    help: b("This first step creates context for the practice session. It is not part of any government form.", "本步骤仅用于建立练习上下文，不属于任何政府表格。"),
    fields: []
  },
  {
    id: "personal-1",
    kind: "form",
    title: b("Personal Information 1", "个人信息 1"),
    shortTitle: b("Personal 1", "个人 1"),
    description: b("Practice entering names, identity details and basic biographical information.", "练习填写姓名、身份与基础个人信息。"),
    help: b("Long forms often separate names into structured fields. Use fictional values and keep spelling consistent across sections.", "长表单通常会将姓名拆分为结构化字段。请只使用虚构资料，并保持各章节拼写一致。"),
    fields: [
      { id: "surname", kind: "text", label: b("Surname", "姓"), required: true, sensitive: true, placeholder: b("Example", "示例：Example"), maxLength: 60 },
      { id: "givenName", kind: "text", label: b("Given Name", "名"), required: true, sensitive: true, placeholder: b("Alex", "示例：Alex"), maxLength: 80 },
      { id: "nativeName", kind: "text", label: b("Full Name in Native Alphabet", "本国语言全名"), sensitive: true, placeholder: b("Fictional native-script name", "示例人物"), maxLength: 100 },
      { id: "usedOtherNames", kind: "yesno", label: b("Have you used other names in this fictional scenario?", "在虚构情景中是否使用过其他姓名？"), hint: yesNoHint, required: true },
      { id: "otherSurname", kind: "text", label: b("Other Surname", "其他姓氏"), required: true, condition: { field: "usedOtherNames", equals: "yes" }, maxLength: 60 },
      { id: "otherGivenName", kind: "text", label: b("Other Given Name", "其他名字"), required: true, condition: { field: "usedOtherNames", equals: "yes" }, maxLength: 80 },
      { id: "sex", kind: "select", label: b("Sex used in this practice record", "练习记录中的性别"), required: true, options: [option("FEMALE", "Female", "女"), option("MALE", "Male", "男"), option("OTHER", "Another fictional selection", "其他虚构选项")] },
      { id: "maritalStatus", kind: "select", label: b("Marital Status", "婚姻状况"), required: true, options: [option("SINGLE", "Single", "未婚"), option("MARRIED", "Married", "已婚"), option("PARTNERED", "Partnered", "伴侣关系"), option("DIVORCED", "Divorced", "离异"), option("WIDOWED", "Widowed", "丧偶")] },
      { id: "dateOfBirth", kind: "date", label: b("Date of Birth", "出生日期"), required: true, sensitive: true }
    ]
  },
  {
    id: "personal-2",
    kind: "form",
    title: b("Personal Information 2", "个人信息 2"),
    shortTitle: b("Personal 2", "个人 2"),
    description: b("Practice birthplace, nationality and identification fields.", "练习填写出生地、国籍和身份识别字段。"),
    help: b("Use Exampleland and DEMO identifiers when you want values that are visibly fictional.", "建议使用“示例国”和 DEMO 开头的编号，让虚构属性清晰可见。"),
    fields: [
      { id: "birthCity", kind: "text", label: b("City of Birth", "出生城市"), required: true, sensitive: true, placeholder: b("Sample City", "示例城市") },
      { id: "birthState", kind: "text", label: b("State or Province of Birth", "出生省 / 州"), sensitive: true, placeholder: b("Example Province", "示例省份") },
      { id: "birthCountry", kind: "select", label: b("Country or Region of Birth", "出生国家或地区"), required: true, options: countryOptions },
      { id: "nationality", kind: "select", label: b("Nationality", "国籍"), required: true, options: countryOptions },
      { id: "hasOtherNationality", kind: "yesno", label: b("Does this fictional person hold another nationality?", "该虚构人物是否拥有其他国籍？"), hint: yesNoHint, required: true },
      { id: "otherNationality", kind: "select", label: b("Other Nationality", "其他国籍"), required: true, options: countryOptions, condition: { field: "hasOtherNationality", equals: "yes" } },
      { id: "nationalId", kind: "text", label: b("Practice National Identification Number", "练习用身份识别号码"), sensitive: true, fictionalRule: "nationalId", placeholder: b("DEMO-ID-2026", "仅限 DEMO 开头的虚构编号") }
    ]
  },
  {
    id: "address-contact",
    kind: "form",
    title: b("Address and Contact", "地址与联系方式"),
    shortTitle: b("Contact", "联系方式"),
    description: b("Practice structured addresses and fictional contact details.", "练习结构化地址与虚构联系方式。"),
    help: b("Reserved example domains and 555 telephone ranges help keep training data visibly fictional.", "使用 example.com 保留域名和 555 示例号码，可以避免误填真实资料。"),
    fields: [
      { id: "address1", kind: "text", label: b("Street Address Line 1", "街道地址第 1 行"), required: true, sensitive: true, fictionalRule: "address", placeholder: b("100 Example Avenue", "100 Example Avenue") },
      { id: "address2", kind: "text", label: b("Street Address Line 2", "街道地址第 2 行"), sensitive: true, placeholder: b("Demo Apartment 2", "Demo Apartment 2") },
      { id: "homeCity", kind: "text", label: b("City", "城市"), required: true, sensitive: true, placeholder: b("Sample City", "Sample City") },
      { id: "homeState", kind: "text", label: b("State or Province", "省 / 州"), required: true, sensitive: true, placeholder: b("Example Province", "Example Province") },
      { id: "postalCode", kind: "text", label: b("Postal Zone", "邮政编码"), sensitive: true, placeholder: b("DEMO-0100", "DEMO-0100") },
      { id: "homeCountry", kind: "select", label: b("Country or Region", "国家或地区"), required: true, options: countryOptions },
      { id: "mailingSame", kind: "yesno", label: b("Is the practice mailing address the same?", "练习邮寄地址是否相同？"), hint: yesNoHint, required: true },
      { id: "mailingAddress", kind: "textarea", label: b("Practice Mailing Address", "练习邮寄地址"), required: true, sensitive: true, fictionalRule: "address", condition: { field: "mailingSame", equals: "no" }, placeholder: b("200 Sample Road, Demo City", "200 Sample Road, Demo City") },
      { id: "primaryPhone", kind: "tel", label: b("Primary Phone", "主要电话"), required: true, sensitive: true, fictionalRule: "phone", placeholder: b("+1 202-555-0100", "+1 202-555-0100") },
      { id: "secondaryPhone", kind: "tel", label: b("Secondary Phone", "备用电话"), sensitive: true, fictionalRule: "phone", placeholder: b("+1 202-555-0188", "+1 202-555-0188") },
      { id: "practiceEmail", kind: "email", label: b("Practice Email", "练习邮箱"), required: true, sensitive: true, fictionalRule: "email", placeholder: b("alex@example.com", "alex@example.com") }
    ]
  },
  {
    id: "passport",
    kind: "form",
    title: b("Passport Information", "护照信息"),
    shortTitle: b("Passport", "护照"),
    description: b("Practice travel-document fields without uploading or using a real passport.", "在不上传、不使用真实护照的前提下练习证件字段。"),
    help: b("This simulator intentionally has no document upload. Use DEMO123456 for the practice passport number.", "模拟器不会提供证件上传。练习护照号请使用 DEMO123456。"),
    fields: [
      { id: "passportType", kind: "select", label: b("Passport Type", "护照类型"), required: true, options: [option("REGULAR", "Regular practice passport", "普通练习护照"), option("OFFICIAL", "Official-type practice document", "公务类型练习证件"), option("OTHER", "Other practice document", "其他练习证件")] },
      { id: "passportNumber", kind: "text", label: b("Practice Passport Number", "练习护照号码"), required: true, sensitive: true, fictionalRule: "passport", placeholder: b("DEMO123456", "仅输入 DEMO123456 等虚构号码") },
      { id: "passportBookNumber", kind: "text", label: b("Passport Book Number", "护照本编号"), sensitive: true, fictionalRule: "passport", placeholder: b("DEMOBOOK01", "DEMOBOOK01") },
      { id: "passportAuthority", kind: "text", label: b("Country or Authority", "签发国家或机构"), required: true, placeholder: b("Exampleland Practice Authority", "示例国练习签发机构") },
      { id: "passportIssueCity", kind: "text", label: b("City of Issuance", "签发城市"), required: true, placeholder: b("Sample City", "Sample City") },
      { id: "passportIssueCountry", kind: "select", label: b("Country of Issuance", "签发国家"), required: true, options: countryOptions },
      { id: "passportIssueDate", kind: "date", label: b("Issuance Date", "签发日期"), required: true },
      { id: "passportExpiration", kind: "date", label: b("Expiration Date", "到期日期"), required: true },
      { id: "lostPassport", kind: "yesno", label: b("Has the fictional person ever lost a passport?", "该虚构人物是否曾遗失护照？"), hint: yesNoHint, required: true },
      { id: "lostPassportExplanation", kind: "textarea", label: b("Practice loss explanation", "练习用遗失说明"), required: true, condition: { field: "lostPassport", equals: "yes" }, placeholder: b("Describe a clearly fictional event.", "请描述一个明显虚构的事件。") }
    ]
  },
  {
    id: "travel",
    kind: "form",
    title: b("Travel Information", "旅行信息"),
    shortTitle: b("Travel", "旅行"),
    description: b("Build a fictional trip plan and practice date relationships.", "创建虚构行程并练习日期先后关系。"),
    help: b("The simulator validates basic chronology only. It does not assess whether a travel purpose is appropriate.", "模拟器只校验基础时间逻辑，不判断旅行目的是否适当。"),
    fields: [
      { id: "practiceVisaCategory", kind: "select", label: b("Practice Visa Scenario", "练习签证情景"), required: true, options: [option("B1_B2", "B1/B2 visitor practice", "B1/B2 访问练习"), option("F1", "F-1 student practice", "F-1 学生练习"), option("J1", "J-1 exchange practice", "J-1 交流练习"), option("OTHER", "Other fictional scenario", "其他虚构情景")] },
      { id: "tripPurpose", kind: "select", label: b("Purpose of Practice Trip", "练习旅行目的"), required: true, options: [option("EDUCATIONAL_PRACTICE", "Educational Practice", "教育练习"), option("SAMPLE_VISIT", "Sample Visit", "示例访问"), option("DEMO_BUSINESS", "Demo Business Meeting", "虚构商务会议"), option("OTHER", "Other fictional purpose", "其他虚构目的")] },
      { id: "arrivalDate", kind: "date", label: b("Intended Arrival Date", "预计到达日期"), required: true },
      { id: "stayLength", kind: "text", label: b("Intended Length of Stay", "预计停留时间"), required: true, placeholder: b("10 DAYS", "10 DAYS") },
      { id: "tripAddress", kind: "text", label: b("Address During Practice Trip", "练习旅行期间地址"), required: true, sensitive: true, fictionalRule: "address", placeholder: b("100 Example Avenue, Sample City", "100 Example Avenue, Sample City") },
      { id: "tripPayer", kind: "select", label: b("Person or Entity Paying for Trip", "旅行费用承担方"), required: true, options: [option("SELF", "Fictional applicant", "虚构申请人本人"), option("FAMILY", "Fictional family member", "虚构家庭成员"), option("ORGANIZATION", "Sample organization", "示例机构")] },
      { id: "specificPlans", kind: "yesno", label: b("Does this practice scenario have specific travel plans?", "该练习情景是否已有具体行程？"), hint: yesNoHint, required: true },
      { id: "arrivalFlight", kind: "text", label: b("Fictional Arrival Flight", "虚构抵达航班"), required: true, condition: { field: "specificPlans", equals: "yes" }, placeholder: b("DEMO101", "DEMO101") },
      { id: "arrivalCity", kind: "text", label: b("Fictional Arrival City", "虚构抵达城市"), required: true, condition: { field: "specificPlans", equals: "yes" }, placeholder: b("Sample City", "Sample City") },
      { id: "departureDate", kind: "date", label: b("Fictional Departure Date", "虚构离境日期"), required: true, condition: { field: "specificPlans", equals: "yes" } },
      { id: "departureCity", kind: "text", label: b("Fictional Departure City", "虚构离境城市"), required: true, condition: { field: "specificPlans", equals: "yes" }, placeholder: b("Example City", "Example City") }
    ]
  },
  {
    id: "companions",
    kind: "form",
    title: b("Travel Companions", "同行人"),
    shortTitle: b("Companions", "同行人"),
    description: b("Practice conditional group and companion records.", "练习团组与同行人条件分支。"),
    help: b("Repeatable records can be added, reordered and removed. Use names such as Taylor Sample.", "同行记录支持添加、排序和删除。请使用 Taylor Sample 等虚构姓名。"),
    fields: [
      { id: "hasCompanions", kind: "yesno", label: b("Is anyone traveling with the fictional person?", "是否有人与虚构人物同行？"), hint: yesNoHint, required: true },
      { id: "isGroupTravel", kind: "yesno", label: b("Is this a fictional group or organization trip?", "是否属于虚构团组或机构出行？"), hint: yesNoHint, required: true, condition: { field: "hasCompanions", equals: "yes" } },
      { id: "groupName", kind: "text", label: b("Fictional Group Name", "虚构团组名称"), required: true, condition: { field: "isGroupTravel", equals: "yes" }, placeholder: b("Sample Learning Group", "Sample Learning Group") },
      {
        id: "companions",
        kind: "repeater",
        label: b("Travel Companions", "同行人记录"),
        required: true,
        condition: { field: "hasCompanions", equals: "yes" },
        addLabel: b("Add fictional companion", "添加虚构同行人"),
        columns: [
          { key: "surname", label: b("Surname", "姓") },
          { key: "givenName", label: b("Given Name", "名") },
          { key: "relationship", label: b("Relationship", "关系"), type: "select", options: relationshipOptions }
        ]
      }
    ]
  },
  {
    id: "previous-travel",
    kind: "form",
    title: b("Previous Travel", "以往旅行"),
    shortTitle: b("History", "旅行历史"),
    description: b("Practice historical travel and visa-related conditional questions.", "练习历史旅行及签证相关条件问题。"),
    help: b("No answer is scored. In a real application, users must answer official questions truthfully.", "本平台不会对答案评分。正式申请时必须如实回答官方问题。"),
    fields: [
      { id: "hasPreviousTravel", kind: "yesno", label: b("Does the fictional scenario include previous related travel?", "虚构情景中是否存在以往相关旅行？"), hint: yesNoHint, required: true },
      { id: "previousTravelDate", kind: "date", label: b("Most Recent Fictional Travel Date", "最近一次虚构旅行日期"), required: true, condition: { field: "hasPreviousTravel", equals: "yes" } },
      { id: "previousStayLength", kind: "text", label: b("Fictional Length of Stay", "虚构停留时间"), required: true, condition: { field: "hasPreviousTravel", equals: "yes" }, placeholder: b("7 DAYS", "7 DAYS") },
      { id: "hadPreviousVisa", kind: "yesno", label: b("Did the fictional person previously receive a visa?", "虚构人物是否曾获得签证？"), hint: yesNoHint, required: true },
      { id: "previousVisaNumber", kind: "text", label: b("Practice Previous Visa Number", "练习用旧签证号码"), required: true, sensitive: true, fictionalRule: "passport", condition: { field: "hadPreviousVisa", equals: "yes" }, placeholder: b("DEMO-VISA-01", "DEMO-VISA-01") },
      { id: "wasRefused", kind: "yesno", label: b("Does this fictional scenario include a previous refusal?", "该虚构情景是否包含过往拒绝记录？"), hint: yesNoHint, required: true },
      { id: "refusalExplanation", kind: "textarea", label: b("Practice Explanation", "练习说明"), required: true, condition: { field: "wasRefused", equals: "yes" }, placeholder: b("Describe a fictional event without seeking an eligibility assessment.", "请描述虚构事件，不要求系统判断资格。") }
    ]
  },
  {
    id: "us-contact",
    kind: "form",
    title: b("U.S. Contact Practice", "美国联系人练习"),
    shortTitle: b("U.S. Contact", "美国联系人"),
    description: b("Practice contact-person and organization fields with fictional details.", "使用虚构资料练习联系人和机构字段。"),
    help: b("Every contact on this page must be fictional. Sample Training Center is provided as a safe example.", "本页所有联系人必须是虚构对象，可使用 Sample Training Center。"),
    fields: [
      { id: "usContactName", kind: "text", label: b("Contact Person Name", "联系人姓名"), required: true, sensitive: true, placeholder: b("Taylor Sample", "Taylor Sample") },
      { id: "usOrganization", kind: "text", label: b("Organization Name", "机构名称"), required: true, placeholder: b("Sample Training Center", "Sample Training Center") },
      { id: "usRelationship", kind: "select", label: b("Relationship", "关系"), required: true, options: relationshipOptions },
      { id: "usContactAddress", kind: "text", label: b("Practice Address", "练习地址"), required: true, sensitive: true, fictionalRule: "address", placeholder: b("100 Example Avenue, Sample City", "100 Example Avenue, Sample City") },
      { id: "usContactPhone", kind: "tel", label: b("Practice Phone", "练习电话"), required: true, sensitive: true, fictionalRule: "phone", placeholder: b("+1 202-555-0142", "+1 202-555-0142") },
      { id: "usContactEmail", kind: "email", label: b("Practice Email", "练习邮箱"), required: true, sensitive: true, fictionalRule: "email", placeholder: b("contact@example.com", "contact@example.com") }
    ]
  },
  {
    id: "family",
    kind: "form",
    title: b("Family Information", "家庭信息"),
    shortTitle: b("Family", "家庭"),
    description: b("Practice family record structure using fictional people only.", "仅使用虚构人物练习家庭记录结构。"),
    help: b("Do not enter real relatives. Example, Sample and Demo are recommended practice surnames.", "不要填写真实亲属资料，建议使用 Example、Sample、Demo 等练习姓氏。"),
    fields: [
      { id: "fatherSurname", kind: "text", label: b("Father's Surname", "父亲姓氏"), required: true, sensitive: true, placeholder: b("Example", "Example") },
      { id: "fatherGivenName", kind: "text", label: b("Father's Given Name", "父亲名字"), required: true, sensitive: true, placeholder: b("Morgan", "Morgan") },
      { id: "fatherDob", kind: "date", label: b("Father's Date of Birth", "父亲出生日期"), sensitive: true },
      { id: "motherSurname", kind: "text", label: b("Mother's Surname", "母亲姓氏"), required: true, sensitive: true, placeholder: b("Sample", "Sample") },
      { id: "motherGivenName", kind: "text", label: b("Mother's Given Name", "母亲名字"), required: true, sensitive: true, placeholder: b("Jordan", "Jordan") },
      { id: "motherDob", kind: "date", label: b("Mother's Date of Birth", "母亲出生日期"), sensitive: true },
      { id: "spouseName", kind: "text", label: b("Fictional Spouse or Partner Name", "虚构配偶或伴侣姓名"), required: true, sensitive: true, condition: { field: "maritalStatus", oneOf: ["MARRIED", "PARTNERED"] }, placeholder: b("Casey Example", "Casey Example") },
      { id: "spouseDob", kind: "date", label: b("Fictional Spouse or Partner Date of Birth", "虚构配偶或伴侣出生日期"), required: true, sensitive: true, condition: { field: "maritalStatus", oneOf: ["MARRIED", "PARTNERED"] } },
      { id: "hasImmediateRelatives", kind: "yesno", label: b("Does the fictional scenario include immediate relatives at the destination?", "虚构情景中是否有直系亲属位于目的地？"), hint: yesNoHint, required: true },
      { id: "relativeExplanation", kind: "textarea", label: b("Fictional Relative Details", "虚构亲属说明"), required: true, condition: { field: "hasImmediateRelatives", equals: "yes" }, placeholder: b("Name, relationship and fictional status.", "填写虚构姓名、关系和状态。") }
    ]
  },
  {
    id: "work-education",
    kind: "form",
    title: b("Work / Education / Training", "工作 / 教育 / 培训"),
    shortTitle: b("Work / Study", "工作教育"),
    description: b("Practice current activity, timelines and repeatable history records.", "练习当前活动、时间线和可重复经历记录。"),
    help: b("Timeline records help demonstrate repeatable groups and chronology validation. Keep every employer and school fictional.", "时间线记录用于演示动态分组和时间逻辑，请确保所有单位和学校均为虚构。"),
    fields: [
      { id: "occupation", kind: "select", label: b("Primary Occupation", "主要职业"), required: true, options: [option("STUDENT", "Student", "学生"), option("EMPLOYED", "Employed in a fictional role", "虚构在职"), option("NOT_EMPLOYED", "Not employed", "未就业"), option("RETIRED", "Retired", "退休"), option("OTHER", "Other", "其他")] },
      { id: "employerName", kind: "text", label: b("Employer or School Name", "雇主或学校名称"), required: true, placeholder: b("Example Learning Studio", "Example Learning Studio") },
      { id: "practiceSchoolName", kind: "text", label: b("Practice School or Sponsor Name", "练习学校或项目机构"), required: true, condition: { field: "practiceVisaCategory", oneOf: ["F1", "J1"] }, placeholder: b("Example Learning University", "Example Learning University") },
      { id: "practiceSevisId", kind: "text", label: b("Practice SEVIS ID", "练习 SEVIS ID"), required: true, sensitive: true, fictionalRule: "nationalId", condition: { field: "practiceVisaCategory", oneOf: ["F1", "J1"] }, placeholder: b("DEMO-SEVIS-001", "DEMO-SEVIS-001") },
      { id: "practiceProgramNumber", kind: "text", label: b("Practice Exchange Program Number", "练习交流项目编号"), required: true, sensitive: true, fictionalRule: "nationalId", condition: { field: "practiceVisaCategory", equals: "J1" }, placeholder: b("DEMO-PROGRAM-01", "DEMO-PROGRAM-01") },
      { id: "employerAddress", kind: "text", label: b("Practice Address", "练习地址"), required: true, sensitive: true, fictionalRule: "address", placeholder: b("300 Sample Street, Demo City", "300 Sample Street, Demo City") },
      { id: "workStartDate", kind: "date", label: b("Start Date", "开始日期"), required: true },
      { id: "incomeNotApplicable", kind: "checkbox", label: b("Monthly income is not applicable in this practice", "本次练习不适用月收入"), hint: b("Check this for a student or other fictional scenario without monthly income.", "学生或无月收入的虚构情景可勾选。") },
      { id: "monthlyIncome", kind: "number", label: b("Fictional Monthly Income", "虚构月收入"), required: true, condition: { field: "incomeNotApplicable", notEquals: true }, placeholder: b("5000", "5000") },
      { id: "duties", kind: "textarea", label: b("Briefly Describe Duties or Study", "简要描述职责或学习内容"), required: true, maxLength: 800, placeholder: b("Practice curriculum design and sample workshops.", "示例课程设计与虚构培训活动。") },
      { id: "hasPreviousEmployment", kind: "yesno", label: b("Add fictional previous employment?", "是否添加虚构的过往工作？"), hint: yesNoHint, required: true },
      {
        id: "previousEmployment",
        kind: "repeater",
        label: b("Previous Employment", "过往工作经历"),
        required: true,
        condition: { field: "hasPreviousEmployment", equals: "yes" },
        addLabel: b("Add fictional employer", "添加虚构雇主"),
        columns: [
          { key: "name", label: b("Employer", "雇主") },
          { key: "role", label: b("Role", "职位") },
          { key: "start", label: b("Start", "开始"), type: "date" },
          { key: "end", label: b("End", "结束"), type: "date" }
        ]
      },
      {
        id: "educationHistory",
        kind: "repeater",
        label: b("Education History", "教育经历"),
        addLabel: b("Add fictional school", "添加虚构学校"),
        columns: [
          { key: "school", label: b("School", "学校") },
          { key: "subject", label: b("Field of Study", "专业") },
          { key: "start", label: b("Start", "开始"), type: "date" },
          { key: "end", label: b("End", "结束"), type: "date" }
        ]
      },
      { id: "languages", kind: "stringList", label: b("Languages", "语言能力"), addLabel: b("Add language", "添加语言"), placeholder: b("Example Language", "示例语言") },
      { id: "countriesVisited", kind: "stringList", label: b("Countries or Regions Visited", "到访国家或地区"), addLabel: b("Add destination", "添加国家或地区"), placeholder: b("Exampleland", "示例国") }
    ]
  },
  {
    id: "additional-background",
    kind: "form",
    title: b("Additional Background", "补充背景"),
    shortTitle: b("Background", "补充背景"),
    description: b("Practice neutral organization, skill and service-history questions.", "练习中性表达的组织、技能与服役经历问题。"),
    help: b("These are generic training prompts. They are not a complete or verbatim copy of any official questionnaire.", "这些是通用练习题，不是任何官方问卷的完整或逐字复制。"),
    fields: [
      { id: "belongsToOrganizations", kind: "yesno", label: b("Does the fictional scenario include membership in an organization?", "虚构情景中是否包含组织成员经历？"), hint: yesNoHint, required: true },
      { id: "organizationExplanation", kind: "textarea", label: b("Fictional Organization Details", "虚构组织说明"), required: true, condition: { field: "belongsToOrganizations", equals: "yes" } },
      { id: "hasSpecialSkills", kind: "yesno", label: b("Does the fictional scenario include specialized technical training?", "虚构情景中是否包含专业技术训练？"), hint: yesNoHint, required: true },
      { id: "skillExplanation", kind: "textarea", label: b("Fictional Training Details", "虚构训练说明"), required: true, condition: { field: "hasSpecialSkills", equals: "yes" } },
      { id: "hasMilitaryService", kind: "yesno", label: b("Does the fictional scenario include military service?", "虚构情景中是否包含服役经历？"), hint: yesNoHint, required: true },
      { id: "militaryExplanation", kind: "textarea", label: b("Fictional Service Details", "虚构服役说明"), required: true, condition: { field: "hasMilitaryService", equals: "yes" } }
    ]
  },
  {
    id: "security-practice",
    kind: "form",
    title: b("Security and Eligibility Practice Questions", "安全与资格练习问题"),
    shortTitle: b("Eligibility", "资格练习"),
    description: b("Generic compliance questions for interaction practice only; no eligibility judgment is produced.", "仅用于交互练习的通用合规问题，不生成任何资格判断。"),
    help: b("A Yes answer only opens a practice explanation. This tool never labels an answer as high risk, low risk, approvable or refusable.", "选择 Yes 只会展开练习说明，本工具不会输出高低风险、获批或拒绝判断。"),
    fields: [
      { id: "securityHealth", kind: "yesno", label: b("Does the fictional scenario include a health condition requiring an official explanation?", "虚构情景中是否包含需要正式说明的健康情况？"), hint: yesNoHint, required: true },
      { id: "securityHealthExplanation", kind: "textarea", label: b("Practice Explanation", "练习说明"), required: true, condition: { field: "securityHealth", equals: "yes" } },
      { id: "securityLegal", kind: "yesno", label: b("Does the fictional scenario include an arrest, charge or court matter?", "虚构情景中是否包含逮捕、指控或法院事项？"), hint: yesNoHint, required: true },
      { id: "securityLegalExplanation", kind: "textarea", label: b("Practice Explanation", "练习说明"), required: true, condition: { field: "securityLegal", equals: "yes" } },
      { id: "securityImmigration", kind: "yesno", label: b("Does the fictional scenario include an immigration-status violation?", "虚构情景中是否包含移民身份违规？"), hint: yesNoHint, required: true },
      { id: "securityImmigrationExplanation", kind: "textarea", label: b("Practice Explanation", "练习说明"), required: true, condition: { field: "securityImmigration", equals: "yes" } },
      { id: "securityMisrepresentation", kind: "yesno", label: b("Does the fictional scenario include false information used to obtain a benefit?", "虚构情景中是否包含通过虚假信息获取利益？"), hint: yesNoHint, required: true },
      { id: "securityMisrepresentationExplanation", kind: "textarea", label: b("Practice Explanation", "练习说明"), required: true, condition: { field: "securityMisrepresentation", equals: "yes" } },
      { id: "securitySafety", kind: "yesno", label: b("Does the fictional scenario include conduct that could endanger another person?", "虚构情景中是否包含可能危害他人的行为？"), hint: yesNoHint, required: true },
      { id: "securitySafetyExplanation", kind: "textarea", label: b("Practice Explanation", "练习说明"), required: true, condition: { field: "securitySafety", equals: "yes" } },
      { id: "securityOther", kind: "yesno", label: b("Is there another fictional compliance matter to explain?", "是否还有其他需要说明的虚构合规事项？"), hint: yesNoHint, required: true },
      { id: "securityOtherExplanation", kind: "textarea", label: b("Practice Explanation", "练习说明"), required: true, condition: { field: "securityOther", equals: "yes" } }
    ]
  },
  {
    id: "review",
    kind: "review",
    title: b("Review Practice Answers", "复核练习答案"),
    shortTitle: b("Review", "复核"),
    description: b("Review every section, find incomplete items and jump back to edit.", "按章节复核、查找未完成项，并返回对应章节编辑。"),
    help: b("Review status describes this local practice only. It is not an assessment of a real application.", "Review 状态仅描述本地练习，不代表真实申请评估。"),
    fields: []
  },
  {
    id: "print",
    kind: "print",
    title: b("Print Practice Copy", "打印练习副本"),
    shortTitle: b("Print", "打印"),
    description: b("Create an unmistakably unofficial training copy for your own device.", "生成带醒目标识的非官方训练副本并保存在自己的设备。"),
    help: b("The print view contains no barcode, confirmation number, government identifier, signature or seal.", "打印页不含条形码、确认码、政府编号、签名或印章。"),
    fields: []
  },
  {
    id: "finished",
    kind: "finished",
    title: b("You completed the local practice", "您已完成本地练习"),
    shortTitle: b("Finished", "完成"),
    description: b("No application was submitted. Your practice draft remains in this browser.", "没有提交任何申请；练习草稿仍保存在当前浏览器。"),
    help: b("You can return to Review, print a practice copy or start another fictional session.", "可以返回 Review、打印练习副本或创建新的虚构练习。"),
    fields: []
  }
];

export const FORM_STEPS = STEPS.filter((step) => step.kind === "form");
export const ALL_FIELDS = FORM_STEPS.flatMap((step) => step.fields);

export function stepIndexById(id: string): number {
  const index = STEPS.findIndex((step) => step.id === id);
  return index < 0 ? 0 : index;
}

export function fieldById(id: string): FieldConfig | undefined {
  return ALL_FIELDS.find((field) => field.id === id);
}
