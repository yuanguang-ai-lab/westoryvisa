(function attachDocFlowIntakeI18n(global) {
  const SECTION_KEYS = {
    "申请信息": "application",
    "基础信息": "personal",
    "护照信息": "passport",
    "地址 / 电话 / 社交媒体": "contact",
    "旅行信息": "travel",
    "同行人": "companions",
    "在美停留地址": "usAddress",
    "以往赴美记录": "usHistory",
    "美国联系人": "usContact",
    "家庭信息": "family",
    "工作 / 教育 / 培训": "workEducation",
    "补充经历": "additional",
    "F/J 补充联系人": "fjContacts",
    "SEVIS / 学生信息": "sevis",
    "健康与背景": "healthSecurity",
    "犯罪背景": "criminalSecurity",
    "国家安全与人权": "nationalSecurity",
    "移民记录": "immigration",
    "其他背景问题": "otherSecurity",
    "照片与协助填写": "photoPreparer"
  };

  const ENGLISH_FIELDS = {
    "application.consulateCountry": "Country/region of application",
    "application.consulateCity": "Consular location",
    "application.applicantRole": "Applicant role",
    "personal.surname": "Surnames as shown in passport",
    "personal.givenNames": "Given names as shown in passport",
    "personal.nativeName": "Full name in native alphabet",
    "personal.sex": "Sex at birth",
    "personal.dateOfBirth": "Date of birth",
    "personal.birthCity": "City of birth",
    "personal.birthRegion": "State/province of birth",
    "personal.birthCountry": "Country/region of birth",
    "personal.nationality": "Current nationality",
    "personal.nationalId": "National identification number",
    "travel.purposeSummary": "Purpose of travel to the United States",
    "contact.usStreet1": "U.S. address line 1",
    "contact.usStreet2": "U.S. address line 2",
    "contact.usCity": "U.S. city",
    "contact.usState": "U.S. state",
    "contact.usPostalCode": "U.S. ZIP code",
    "contact.homeStreet1": "Home address line 1",
    "contact.homeStreet2": "Home address line 2",
    "contact.homeCity": "Home city",
    "contact.homeRegion": "Home state/province",
    "contact.homePostalCode": "Home postal code",
    "contact.homeCountry": "Home country/region",
    "contact.primaryPhone": "Primary phone number",
    "contact.secondaryPhone": "Secondary phone number",
    "contact.workPhone": "Work phone number",
    "contact.email": "Email address",
    "passport.number": "Passport/travel document number",
    "passport.issuingAuthority": "Issuing country/authority",
    "passport.issueCity": "City where issued",
    "passport.issueRegion": "State/province where issued",
    "passport.issueCountry": "Country/region where issued",
    "passport.issueDate": "Issuance date",
    "passport.expiration": "Expiration date",
    "education.sevisId": "SEVIS ID",
    "education.schoolName": "U.S. school name",
    "education.programName": "Course of study",
    "education.schoolStreet1": "School address line 1",
    "education.schoolStreet2": "School address line 2",
    "education.schoolCity": "School city",
    "education.schoolState": "School state",
    "education.schoolPostalCode": "School ZIP code",
    "education.programStartDate": "Program start date",
    "education.programEndDate": "Program end date",
    "education.programNumber": "DS-2019 program number",
    "education.sponsorName": "Program sponsor",
    "education.programCategory": "J program category"
  };

  const BASE_MESSAGES = {
    loading: "Loading client information form",
    submittedKicker: "Submitted",
    submittedTitle: "Information sent to your advisor",
    submittedBody: "Your answers were saved to the case. Your advisor will review the important fields; you do not need to submit again.",
    submittedNote: "This page does not submit a DS-160 and does not process fees or legal declarations.",
    supplement: "Client information",
    introTitle: "Only provide information missing from the documents",
    introBody: "Your advisor has organized the documents already provided. Answer truthfully. Your advisor will handle formatting and final review. For a text field that does not apply, enter D; the system will convert it to DOES NOT APPLY.",
    part: "Part {current} of {total}",
    identityKicker: "Identity check",
    identityTitle: "Enter the applicant's name first",
    identityBody: "The applicant in the advisor's file is “{name}”. If this is not you, stop and ask the advisor to confirm the link.",
    applicantName: "Applicant name",
    applicantNamePlaceholder: "Enter the applicant's name",
    basicDetails: "Additional details",
    basicDetailsBody: "These details were not reliably found in the existing documents.",
    emptyTitle: "Nothing is required in this part",
    emptyBody: "You can continue to the next part.",
    previous: "Previous",
    next: "Save and continue",
    submit: "Send to advisor",
    submitting: "Submitting…",
    safety: "The information is used only to help your advisor prepare a DS-160 draft. This page does not provide legal advice, predict a visa result, connect to a U.S. government website, sign, or submit an application.",
    choose: "Select",
    fill: "Enter information",
    explain: "Enter the facts",
    materialProvided: "Found in documents",
    loaded: "Loaded",
    optional: "Optional. Your advisor can review it before the final form is filled.",
    addRecord: "+ Add record",
    record: "Record",
    recordOptional: "Optional; add when applicable",
    recordOne: "Add at least one complete record",
    recordMany: "Add records as needed",
    removeRecord: "Remove record {number}",
    selectSocial: "Select a platform and enter the account identifier",
    selectSocialBody: "One complete account is enough. You may add more. Never enter a password.",
    socialHandle: "Username / handle",
    socialOverview: "Review the platforms listed on the DS-160",
    checkFacts: "Answer from the applicant's actual facts. Your advisor must compare this item with the exact English wording shown in CEAC.",
    fieldRequired: "Enter “{label}”.",
    choiceRequired: "Select an answer for “{label}”.",
    recordMinimum: "Add at least {minimum} complete “{label}” record(s).",
    recordIncomplete: "Complete or remove the unfinished “{label}” record.",
    socialRequired: "Select a platform you used and enter its username or handle.",
    companionsRequired: "If nobody is traveling with you, select No above. Otherwise add at least one complete traveler.",
    invalidSelect: "“{label}” does not match a DS-160 option. Use the official English name, for example INDIA.",
    identityRequired: "Enter the applicant's name so your advisor can match this response to the correct case.",
    submitFailed: "Submission failed. Try again later.",
    draftFailed: "Draft could not be saved. Try again later.",
    unavailableKicker: "Link unavailable",
    unavailableTitle: "Ask your advisor for a new link",
    unavailableBody: "This link has expired or is no longer valid."
  };

  const PACKS = {
    "zh-CN": {
      lang: "zh-CN",
      messages: {
        loading: "正在读取客户补充表",
        submittedKicker: "已提交", submittedTitle: "资料已发送给顾问",
        submittedBody: "你的回答已经写入客户档案。文案老师或签证顾问会继续核对关键字段；无需再次提交。",
        submittedNote: "本页面不会提交真实 DS-160，也不会处理费用或法律声明。",
        supplement: "客户资料补充", introTitle: "只需补充材料里没有的信息",
        introBody: "顾问已经上传并整理现有材料。这里不会重复询问材料中已识别的内容；请按真实情况回答，专业格式与最终核查由顾问完成。不适用的文字字段可直接填写 D，系统会自动转为 DOES NOT APPLY。",
        part: "第 {current} / {total} 部分", identityKicker: "档案核对",
        identityTitle: "请先填写本次申请人的姓名",
        identityBody: "顾问档案中的申请人为“{name}”。如果不是本人，请停止填写并联系顾问确认链接。",
        applicantName: "申请人姓名", applicantNamePlaceholder: "请输入申请人姓名",
        basicDetails: "基础资料补充", basicDetailsBody: "以下内容没有从现有材料中稳定识别到。",
        emptyTitle: "这一部分目前无需补充", emptyBody: "可以直接进入下一部分。",
        previous: "上一步", next: "保存并继续", submit: "提交给顾问", submitting: "正在提交…",
        safety: "资料仅用于当前顾问整理 DS-160 初稿，不提供法律建议，不预测签证结果，不连接或提交至美国政府网站。",
        choose: "请选择", fill: "请填写", explain: "请按实际情况说明",
        materialProvided: "材料已提供", loaded: "已读取",
        optional: "当前没有资料可先跳过，顾问会在最终填写前处理。",
        addRecord: "+ 添加一条", record: "记录", recordOptional: "可选，按实际情况添加",
        recordOne: "至少填写 1 条", recordMany: "可按实际情况添加多条",
        removeRecord: "删除第 {number} 条记录", selectSocial: "选择平台并填写账号标识",
        selectSocialBody: "只填一个完整账号即可；多个账号也可以添加。不要填写密码。",
        socialHandle: "用户名 / Handle", socialOverview: "先查看页面列出的平台",
        checkFacts: "请按申请人的真实事实回答，并由顾问对照 CEAC 当次英文原题核查。",
        fieldRequired: "请填写“{label}”。", choiceRequired: "请选择“{label}”的答案。",
        recordMinimum: "请至少完整填写 {minimum} 条“{label}”。",
        recordIncomplete: "请补全尚未填写完整的“{label}”，或删除该空白记录。",
        socialRequired: "选择一个使用过的平台并填写用户名即可继续。",
        companionsRequired: "如果没有同行人，请在上一题选择“没有同行人”；如有同行人，请至少完整填写一位。",
        invalidSelect: "“{label}”无法匹配 DS-160 的下拉选项，请填写官网使用的英文名称，例如 CHINA。",
        identityRequired: "请先填写申请人姓名，以便顾问核对客户档案。",
        submitFailed: "资料提交失败，请稍后重试。", draftFailed: "草稿保存失败，请稍后重试。",
        unavailableKicker: "链接不可用", unavailableTitle: "请联系顾问重新发送",
        unavailableBody: "该补充链接已失效或过期。"
      },
      sections: {},
      fields: {},
      fieldHints: {},
      questions: {},
      choices: {}
    },
    "en-IN": {
      lang: "en",
      messages: BASE_MESSAGES,
      sections: {
        application: "Application details", personal: "Personal information",
        passport: "Passport information", contact: "Address, phone and social media",
        travel: "Travel information", companions: "Travel companions",
        usAddress: "Address in the United States", usHistory: "Previous U.S. travel",
        usContact: "U.S. contact", family: "Family information",
        workEducation: "Work, education and training", additional: "Additional history",
        fjContacts: "F/J additional contacts", sevis: "SEVIS / student information",
        healthSecurity: "Health and background", criminalSecurity: "Criminal background",
        nationalSecurity: "Security and human rights", immigration: "Immigration history",
        otherSecurity: "Other background questions", photoPreparer: "Photo and preparer"
      },
      fields: {
        ...ENGLISH_FIELDS,
        "personal.nationalId": "Aadhaar number or reviewed national ID",
        "contact.homeRegion": "State / union territory",
        "contact.homePostalCode": "PIN code",
        "contact.primaryPhone": "Indian mobile number"
      },
      fieldHints: {
        "personal.nationalId": "Enter the 12-digit Aadhaar number only after the applicant confirms it is the national ID to use. PAN requires advisor review.",
        "contact.homePostalCode": "Enter the 6-digit PIN code.",
        "contact.primaryPhone": "Enter a 10-digit Indian mobile number; +91 will be normalized for CEAC."
      },
      questions: {},
      choices: {
        yes: "Yes", no: "No", unknown: "Not sure — ask my advisor",
        self: "Self", other_person: "Another person",
        present_employer: "Present employer", us_employer: "U.S. employer",
        other_organization: "Other organization"
      }
    },
    "es-MX": {
      lang: "es",
      messages: {
        ...BASE_MESSAGES,
        loading: "Cargando el formulario del solicitante",
        submittedKicker: "Enviado", submittedTitle: "Información enviada a tu asesor",
        submittedBody: "Tus respuestas se guardaron en el expediente. Tu asesor revisará los campos importantes; no es necesario enviarlas de nuevo.",
        submittedNote: "Esta página no presenta el DS-160 ni procesa pagos o declaraciones legales.",
        supplement: "Información del solicitante",
        introTitle: "Completa solo la información que falta en los documentos",
        introBody: "Tu asesor ya organizó los documentos disponibles. Responde de acuerdo con la realidad. El asesor se encargará del formato y la revisión final. Si un campo de texto no aplica, escribe D; el sistema lo convertirá en DOES NOT APPLY.",
        part: "Parte {current} de {total}", identityKicker: "Verificación del expediente",
        identityTitle: "Escribe primero el nombre del solicitante",
        identityBody: "El solicitante registrado por el asesor es “{name}”. Si no eres esa persona, deja de llenar el formulario y confirma el enlace con tu asesor.",
        applicantName: "Nombre del solicitante", applicantNamePlaceholder: "Escribe el nombre del solicitante",
        basicDetails: "Datos por completar", basicDetailsBody: "Estos datos no se identificaron con suficiente seguridad en los documentos existentes.",
        emptyTitle: "No hay información pendiente en esta sección", emptyBody: "Puedes continuar a la siguiente sección.",
        previous: "Anterior", next: "Guardar y continuar", submit: "Enviar al asesor", submitting: "Enviando…",
        safety: "La información se usa únicamente para ayudar a tu asesor a preparar un borrador del DS-160. Esta página no ofrece asesoría legal, no predice el resultado, no se conecta con un sitio del gobierno de EE. UU. y no firma ni presenta la solicitud.",
        choose: "Selecciona", fill: "Escribe la información", explain: "Describe los hechos",
        materialProvided: "Información encontrada", loaded: "Leído", optional: "Opcional. Tu asesor puede revisarlo antes del llenado final.",
        addRecord: "+ Agregar registro", record: "Registro", recordOptional: "Opcional; agrega lo que corresponda",
        recordOne: "Agrega por lo menos un registro completo", recordMany: "Agrega los registros necesarios",
        removeRecord: "Eliminar el registro {number}", selectSocial: "Selecciona una plataforma y escribe el identificador de la cuenta",
        selectSocialBody: "Una cuenta completa es suficiente. Puedes agregar más. Nunca escribas una contraseña.",
        socialHandle: "Usuario / identificador", socialOverview: "Revisa las plataformas incluidas en el DS-160",
        checkFacts: "Responde con los hechos reales del solicitante. Tu asesor debe comparar esta pregunta con el texto exacto en inglés que aparece en CEAC.",
        fieldRequired: "Completa “{label}”.", choiceRequired: "Selecciona una respuesta para “{label}”.",
        recordMinimum: "Agrega por lo menos {minimum} registro(s) completo(s) de “{label}”.",
        recordIncomplete: "Completa o elimina el registro incompleto de “{label}”.",
        socialRequired: "Selecciona una plataforma que hayas usado y escribe el usuario o identificador.",
        companionsRequired: "Si nadie viaja contigo, selecciona No arriba. Si hay acompañantes, agrega al menos uno completo.",
        invalidSelect: "“{label}” no coincide con una opción del DS-160. Usa el nombre oficial en inglés, por ejemplo MEXICO.",
        identityRequired: "Escribe el nombre del solicitante para vincular las respuestas con el expediente correcto.",
        submitFailed: "No se pudo enviar. Intenta de nuevo más tarde.", draftFailed: "No se pudo guardar el borrador. Intenta de nuevo más tarde.",
        unavailableKicker: "Enlace no disponible", unavailableTitle: "Solicita un enlace nuevo a tu asesor",
        unavailableBody: "Este enlace venció o ya no es válido."
      },
      sections: {
        application: "Datos de la solicitud", personal: "Información personal", passport: "Pasaporte",
        contact: "Domicilio, teléfono y redes sociales", travel: "Información del viaje",
        companions: "Acompañantes de viaje", usAddress: "Domicilio en Estados Unidos",
        usHistory: "Viajes anteriores a Estados Unidos", usContact: "Contacto en Estados Unidos",
        family: "Información familiar", workEducation: "Trabajo, estudios y capacitación",
        additional: "Antecedentes adicionales", fjContacts: "Contactos adicionales F/J",
        sevis: "Información SEVIS / estudiante", healthSecurity: "Salud y antecedentes",
        criminalSecurity: "Antecedentes penales", nationalSecurity: "Seguridad y derechos humanos",
        immigration: "Historial migratorio", otherSecurity: "Otras preguntas de antecedentes",
        photoPreparer: "Fotografía y persona que ayudó"
      },
      fields: {
        ...ENGLISH_FIELDS,
        "personal.surname": "Apellidos tal como aparecen en el pasaporte",
        "personal.givenNames": "Nombres tal como aparecen en el pasaporte",
        "personal.nationalId": "CURP",
        "contact.homeStreet1": "Domicilio — calle y número",
        "contact.homeCity": "Municipio o alcaldía",
        "contact.homeRegion": "Estado",
        "contact.homePostalCode": "Código postal",
        "contact.primaryPhone": "Teléfono de México",
        "passport.number": "Número de pasaporte"
      },
      fieldHints: {
        "personal.nationalId": "Escribe los 18 caracteres de la CURP.",
        "contact.homePostalCode": "Escribe el código postal de 5 dígitos.",
        "contact.primaryPhone": "Escribe los 10 dígitos del número en México; el sistema normalizará +52."
      },
      questions: {
        "personal.other_names": "¿Has usado otros nombres o apellidos?",
        "personal.marital_status": "¿Cuál es tu estado civil?",
        "personal.other_nationalities": "¿Tienes o tuviste otra nacionalidad?",
        "travel.specific_plans": "¿Ya tienes planes específicos de viaje?",
        "travel.payer": "¿Quién pagará el viaje?",
        "companions.has_companions": "¿Viajarán otras personas contigo?",
        "companions.is_group": "¿Viajas como parte de un grupo u organización?",
        "us_history.visited": "¿Has estado antes en Estados Unidos?",
        "us_history.previous_visa": "¿Alguna vez te emitieron una visa de Estados Unidos?",
        "contact.mailing_same_as_home": "¿Tu domicilio postal es igual a tu domicilio particular?",
        "passport.lost_stolen": "¿Alguna vez perdiste un pasaporte o te lo robaron?",
        "work.primary_occupation": "¿Cuál es tu ocupación principal actual?",
        "work.previously_employed": "¿Tuviste empleos anteriores?",
        "work.education_secondary_or_above": "¿Asististe a instituciones educativas de nivel secundario o superior?",
        "preparer.assisted": "¿Alguien te ayudó a llenar esta solicitud?"
      },
      choices: {
        yes: "Sí", no: "No", unknown: "No estoy seguro — consultar al asesor",
        self: "Yo", other_person: "Otra persona", present_employer: "Empleador actual",
        us_employer: "Empleador en Estados Unidos", other_organization: "Otra organización"
      }
    },
    "pt-BR": {
      lang: "pt-BR",
      messages: {
        ...BASE_MESSAGES,
        loading: "Carregando o formulário do solicitante",
        submittedKicker: "Enviado", submittedTitle: "Informações enviadas ao seu consultor",
        submittedBody: "Suas respostas foram salvas no processo. O consultor revisará os campos importantes; não é necessário enviar novamente.",
        submittedNote: "Esta página não envia o DS-160 nem processa pagamentos ou declarações legais.",
        supplement: "Informações do solicitante",
        introTitle: "Preencha apenas o que não consta nos documentos",
        introBody: "Seu consultor já organizou os documentos disponíveis. Responda conforme os fatos. O consultor cuidará do formato e da revisão final. Se um campo de texto não se aplicar, digite D; o sistema converterá para DOES NOT APPLY.",
        part: "Parte {current} de {total}", identityKicker: "Conferência do processo",
        identityTitle: "Primeiro, informe o nome do solicitante",
        identityBody: "O solicitante registrado pelo consultor é “{name}”. Se não for você, pare e confirme o link com o consultor.",
        applicantName: "Nome do solicitante", applicantNamePlaceholder: "Informe o nome do solicitante",
        basicDetails: "Dados a completar", basicDetailsBody: "Estes dados não foram identificados com segurança nos documentos existentes.",
        emptyTitle: "Não há informações pendentes nesta seção", emptyBody: "Você pode seguir para a próxima seção.",
        previous: "Anterior", next: "Salvar e continuar", submit: "Enviar ao consultor", submitting: "Enviando…",
        safety: "As informações são usadas somente para ajudar seu consultor a preparar um rascunho do DS-160. Esta página não oferece aconselhamento jurídico, não prevê o resultado, não se conecta a um site do governo dos EUA e não assina nem envia a solicitação.",
        choose: "Selecione", fill: "Preencha", explain: "Descreva os fatos",
        materialProvided: "Encontrado nos documentos", loaded: "Carregado", optional: "Opcional. O consultor poderá revisar antes do preenchimento final.",
        addRecord: "+ Adicionar registro", record: "Registro", recordOptional: "Opcional; adicione quando aplicável",
        recordOne: "Adicione pelo menos um registro completo", recordMany: "Adicione os registros necessários",
        removeRecord: "Excluir o registro {number}", selectSocial: "Selecione uma plataforma e informe o identificador da conta",
        selectSocialBody: "Uma conta completa é suficiente. Você pode adicionar outras. Nunca informe uma senha.",
        socialHandle: "Usuário / identificador", socialOverview: "Confira as plataformas listadas no DS-160",
        checkFacts: "Responda conforme os fatos reais do solicitante. O consultor deve comparar esta pergunta com o texto exato em inglês exibido no CEAC.",
        fieldRequired: "Preencha “{label}”.", choiceRequired: "Selecione uma resposta para “{label}”.",
        recordMinimum: "Adicione pelo menos {minimum} registro(s) completo(s) de “{label}”.",
        recordIncomplete: "Complete ou exclua o registro incompleto de “{label}”.",
        socialRequired: "Selecione uma plataforma que você usou e informe o usuário ou identificador.",
        companionsRequired: "Se ninguém viajar com você, selecione Não acima. Caso contrário, adicione pelo menos um acompanhante completo.",
        invalidSelect: "“{label}” não corresponde a uma opção do DS-160. Use o nome oficial em inglês, por exemplo BRAZIL.",
        identityRequired: "Informe o nome do solicitante para vincular as respostas ao processo correto.",
        submitFailed: "Não foi possível enviar. Tente novamente mais tarde.", draftFailed: "Não foi possível salvar o rascunho. Tente novamente mais tarde.",
        unavailableKicker: "Link indisponível", unavailableTitle: "Peça um novo link ao seu consultor",
        unavailableBody: "Este link expirou ou não é mais válido."
      },
      sections: {
        application: "Dados da solicitação", personal: "Informações pessoais", passport: "Passaporte",
        contact: "Endereço, telefone e redes sociais", travel: "Informações da viagem",
        companions: "Acompanhantes de viagem", usAddress: "Endereço nos Estados Unidos",
        usHistory: "Viagens anteriores aos Estados Unidos", usContact: "Contato nos Estados Unidos",
        family: "Informações familiares", workEducation: "Trabalho, estudos e treinamento",
        additional: "Histórico adicional", fjContacts: "Contatos adicionais F/J",
        sevis: "Informações SEVIS / estudante", healthSecurity: "Saúde e antecedentes",
        criminalSecurity: "Antecedentes criminais", nationalSecurity: "Segurança e direitos humanos",
        immigration: "Histórico de imigração", otherSecurity: "Outras perguntas de antecedentes",
        photoPreparer: "Foto e pessoa que ajudou"
      },
      fields: {
        ...ENGLISH_FIELDS,
        "personal.surname": "Sobrenomes conforme o passaporte",
        "personal.givenNames": "Nomes conforme o passaporte",
        "personal.nationalId": "CPF ou documento nacional revisado",
        "contact.homeStreet1": "Endereço residencial — logradouro e número",
        "contact.homeCity": "Município",
        "contact.homeRegion": "UF / estado",
        "contact.homePostalCode": "CEP",
        "contact.primaryPhone": "Telefone do Brasil",
        "passport.number": "Número do passaporte"
      },
      fieldHints: {
        "personal.nationalId": "Informe os 11 dígitos do CPF; o sistema verificará os dígitos de controle.",
        "contact.homePostalCode": "Informe o CEP com 8 dígitos.",
        "contact.primaryPhone": "Informe o DDD e o telefone; o sistema normalizará +55."
      },
      questions: {
        "personal.other_names": "Você já usou outros nomes ou sobrenomes?",
        "personal.marital_status": "Qual é o seu estado civil?",
        "personal.other_nationalities": "Você possui ou já possuiu outra nacionalidade?",
        "travel.specific_plans": "Você já fez planos específicos de viagem?",
        "travel.payer": "Quem pagará a viagem?",
        "companions.has_companions": "Outras pessoas viajarão com você?",
        "companions.is_group": "Você viajará como parte de um grupo ou organização?",
        "us_history.visited": "Você já esteve nos Estados Unidos?",
        "us_history.previous_visa": "Você já recebeu um visto dos Estados Unidos?",
        "contact.mailing_same_as_home": "Seu endereço para correspondência é o mesmo endereço residencial?",
        "passport.lost_stolen": "Você já perdeu um passaporte ou teve um passaporte roubado?",
        "work.primary_occupation": "Qual é a sua ocupação principal atual?",
        "work.previously_employed": "Você teve empregos anteriores?",
        "work.education_secondary_or_above": "Você frequentou alguma instituição de ensino médio ou superior?",
        "preparer.assisted": "Alguém ajudou você a preencher esta solicitação?"
      },
      choices: {
        yes: "Sim", no: "Não", unknown: "Não tenho certeza — consultar o consultor",
        self: "Eu", other_person: "Outra pessoa", present_employer: "Empregador atual",
        us_employer: "Empregador nos Estados Unidos", other_organization: "Outra organização"
      }
    }
  };

  const COUNTRY_LOCALES = { CN: "zh-CN", MX: "es-MX", BR: "pt-BR", IN: "en-IN" };
  const DISPLAY_LOCALES = { zh: "zh-CN", en: "en-IN", es: "es-MX", pt: "pt-BR" };

  function locale(data) {
    const explicit = String(global.WestoryLanguage?.locale
      || new URLSearchParams(global.location.search).get("lang")
      || "").toLowerCase().split("-")[0];
    if (DISPLAY_LOCALES[explicit]) return DISPLAY_LOCALES[explicit];
    const requested = String(data?.sourceLocale || "");
    if (PACKS[requested]) return requested;
    return COUNTRY_LOCALES[String(data?.applicationCountry || "CN").toUpperCase()] || "zh-CN";
  }

  function pack(data) {
    return PACKS[locale(data)] || PACKS["zh-CN"];
  }

  function interpolate(value, variables) {
    return String(value || "").replace(/\{([a-zA-Z]+)\}/g, (_match, key) => (
      variables?.[key] === undefined ? "" : String(variables[key])
    ));
  }

  function text(data, key, variables = {}) {
    const selected = pack(data);
    return interpolate(selected.messages[key] || BASE_MESSAGES[key] || key, variables);
  }

  function section(data, value) {
    if (locale(data) === "zh-CN") return value;
    const key = SECTION_KEYS[value];
    return pack(data).sections[key] || value;
  }

  function asciiFallback(value, semanticId) {
    const raw = String(value || "").trim();
    const parts = raw.split("/").map((item) => item.trim()).filter(Boolean);
    const english = [...parts].reverse().find((item) => /^[\x20-\x7e]+$/.test(item));
    if (english) return english;
    if (raw && /^[\x20-\x7e]+$/.test(raw)) return raw;
    return String(semanticId || "information")
      .split(".").pop().replaceAll("_", " ")
      .replace(/\b\w/g, (character) => character.toUpperCase());
  }

  function field(data, item) {
    if (locale(data) === "zh-CN") return item?.label || "";
    return pack(data).fields[item?.id] || ENGLISH_FIELDS[item?.id]
      || asciiFallback(item?.label, item?.id);
  }

  function fieldHint(data, item) {
    if (locale(data) === "zh-CN") return item?.hint || "";
    return pack(data).fieldHints[item?.id] || "";
  }

  function question(data, item) {
    if (locale(data) === "zh-CN") return item?.prompt || "";
    return pack(data).questions[item?.id]
      || asciiFallback(item?.englishPrompt, item?.id);
  }

  function label(data, value, semanticId) {
    return locale(data) === "zh-CN" ? value : asciiFallback(value, semanticId);
  }

  function choice(data, item) {
    if (locale(data) === "zh-CN") return item?.label || item?.value || "";
    return pack(data).choices[item?.value]
      || asciiFallback(item?.label, item?.value);
  }

  function guidance(data, item) {
    if (locale(data) === "zh-CN") return item?.guidance || "";
    return text(data, "checkFacts");
  }

  function applyDocumentLanguage(data) {
    document.documentElement.lang = pack(data).lang;
  }

  global.DocFlowIntakeI18n = {
    locale, text, section, field, fieldHint, question, label, choice, guidance,
    applyDocumentLanguage
  };
})(window);
