(() => {
  "use strict";

  const SERVICE_COUNTRY_STORAGE_KEY = "westoryvisaCountry";
  const LANGUAGE_STORAGE_KEY = "westoryvisaLanguage";
  const LANGUAGE_PARAMETER = "lang";
  const LANGUAGE_ORDER = ["CN", "MX", "BR", "IN"];
  const LANGUAGE_IDS = { CN: "zh-CN", MX: "es", BR: "pt-BR", IN: "en" };
  const LANGUAGE_NAMES = { CN: "中文", MX: "Español", BR: "Português", IN: "English" };

  function remember(key, value) {
    try { window.localStorage.setItem(key, value); } catch (_) {}
  }

  function languageCode(value) {
    const base = String(value || "").toLowerCase().split("-")[0];
    return { zh: "CN", es: "MX", pt: "BR", en: "IN" }[base];
  }

  function selectedLanguageCode() {
    const params = new URLSearchParams(window.location.search);
    const explicit = languageCode(params.get(LANGUAGE_PARAMETER));
    if (explicit) return explicit;
    let saved;
    try { saved = languageCode(window.localStorage.getItem(LANGUAGE_STORAGE_KEY)); } catch (_) {}
    if (saved) return saved;
    return "CN";
  }
  const COUNTRY_ORDER = ["CN", "MX", "BR", "IN"];
  const COUNTRIES = {
    CN: { code: "CN", flag: "🇨🇳", locale: "zh-CN", name: "中国", localName: "中国", language: "简体中文" },
    MX: { code: "MX", flag: "🇲🇽", locale: "es-MX", name: "México", localName: "México", language: "Español" },
    BR: { code: "BR", flag: "🇧🇷", locale: "pt-BR", name: "Brasil", localName: "Brasil", language: "Português" },
    IN: { code: "IN", flag: "🇮🇳", locale: "en-IN", name: "India", localName: "India", language: "English" }
  };

  const UI = {
    CN: {
      choose: "语言",
      title: "选择语言",
      body: "切换整个网站的显示语言，不会改变机构或客户所属国家。",
      close: "关闭语言选择"
    },
    MX: {
      choose: "Idioma",
      title: "Elige un idioma",
      body: "Cambia el idioma de todo el sitio. No modifica el país de la agencia ni del cliente.",
      close: "Cerrar selector de idioma"
    },
    BR: {
      choose: "Idioma",
      title: "Escolha um idioma",
      body: "Altera o idioma de todo o site. Não muda o país da agência nem do cliente.",
      close: "Fechar seletor de idioma"
    },
    IN: {
      choose: "Language",
      title: "Choose a language",
      body: "Change the language across the whole site. This does not change the agency or client country.",
      close: "Close language selector"
    }
  };

  // One source phrase, then natural Spanish, Brazilian Portuguese and English.
  // Chinese is the default display language and the source for other translations.
  const COPY = [
    ["WestoryVisa｜签证顾问的自动化工作流", "WestoryVisa | Automatización para agencias de visas", "WestoryVisa | Automação para agências de vistos", "WestoryVisa | Automation for visa agencies"],
    ["跳到主要内容", "Saltar al contenido principal", "Ir para o conteúdo principal", "Skip to main content"],
    ["自动化亮点", "Automatización", "Automação", "Automation"],
    ["操作台预览", "Vista del sistema", "Visão do sistema", "Workspace preview"],
    ["会员中心", "Membresía", "Assinatura", "Membership"],
    ["预约演示", "Solicitar demostración", "Agendar demonstração", "Book a demo"],
    ["注册/登录账号", "Crear cuenta / Iniciar sesión", "Criar conta / Entrar", "Register / Sign in"],
    ["登录", "Iniciar sesión", "Entrar", "Sign in"],
    ["为签证顾问打造", "Creado para asesores de visas", "Feito para consultores de vistos", "Built for visa consultants"],
    ["6 分钟填完 DS-160 表格", "Completa un DS-160 en 6 minutos", "Preencha um DS-160 em 6 minutos", "Complete a DS-160 in 6 minutes"],
    ["材料自动读取、缺失信息自动归集、字段自动整理、页面逐步辅助填写。", "Lee documentos, detecta datos faltantes, organiza campos y asiste el llenado paso a paso.", "Lê documentos, identifica dados ausentes, organiza campos e auxilia o preenchimento passo a passo.", "Read documents, find missing details, organise fields and assist page-by-page entry."],
    ["顾问无需在文件、聊天记录和多个工具之间反复切换。", "Tu equipo deja de saltar entre archivos, chats y herramientas.", "Sua equipe não precisa alternar entre arquivos, conversas e várias ferramentas.", "Your team no longer has to switch between files, chats and multiple tools."],
    ["约 6 分钟", "Aprox. 6 minutos", "Cerca de 6 minutos", "About 6 minutes"],
    ["实测自动填写一份 DS‑160 所需时间", "Tiempo medido para completar un DS-160 automáticamente", "Tempo medido para preencher um DS-160 automaticamente", "Measured time to complete one DS-160 automatically"],
    ["重复页面操作自动推进", "Avanza las tareas repetitivas del sitio", "Avança tarefas repetitivas no site", "Moves repetitive page tasks forward"],
    ["材料理解与字段结构化", "Comprende documentos y estructura campos", "Entende documentos e estrutura campos", "Understands documents and structures fields"],
    ["查看页面并辅助逐页执行", "Observa la página y asiste paso a paso", "Observa a página e auxilia etapa por etapa", "Observes the page and assists each step"],
    ["约 6 分钟为自动填写一份 DS‑160 的内部实测参考时间，不含验证码、电子签名及最终提交；实际耗时受材料完整度、页面状态和网络环境影响。", "Los 6 minutos son una referencia de pruebas internas. No incluyen CAPTCHA, firma electrónica ni envío final; el tiempo real depende de los documentos, el sitio y la red.", "Os 6 minutos são uma referência de testes internos. Não incluem CAPTCHA, assinatura eletrônica nem envio final; o tempo real depende dos documentos, do site e da rede.", "Six minutes is an internal test reference. It excludes CAPTCHA, electronic signature and final submission; actual time depends on document quality, site status and network conditions."],
    ["操作系统预览", "Vista del sistema", "Visão do sistema", "System preview"],
    ["操作台，一目了然。", "Todo el flujo, a la vista.", "Todo o fluxo, em um só lugar.", "The whole workflow at a glance."],
    ["这里按真实工作台比例还原八步流程。点击左侧步骤查看对应页面；在 DS-160 初稿中还可以切换到 Computer Use 执行台。", "La vista reproduce el flujo real de ocho pasos. Elige una etapa a la izquierda y, desde el borrador DS-160, abre la consola de Computer Use.", "A visualização reproduz o fluxo real de oito etapas. Escolha uma etapa à esquerda e, no rascunho DS-160, abra o painel do Computer Use.", "This preview mirrors the real eight-step flow. Select a step on the left and open Computer Use from the DS-160 draft."],
    ["完整操作台 · 模拟案件", "Sistema completo · Caso de muestra", "Sistema completo · Caso de exemplo", "Full workspace · Sample case"],
    ["操作台", "Panel", "Painel", "Workspace"],
    ["个人中心", "Perfil", "Perfil", "Profile"],
    ["帮助中心", "Ayuda", "Ajuda", "Help"],
    ["机构账号", "Cuenta de agencia", "Conta da agência", "Agency account"],
    ["资料整理中", "Organizando documentos", "Organizando documentos", "Organising documents"],
    ["完整工作流程", "Flujo completo", "Fluxo completo", "Full workflow"],
    ["客户 A-1024 · F-1", "Cliente A-1024 · F-1", "Cliente A-1024 · F-1", "Client A-1024 · F-1"],
    ["客户 A-1024 · B1/B2", "Cliente A-1024 · B1/B2", "Cliente A-1024 · B1/B2", "Client A-1024 · B1/B2"],
    ["客户 B-2086 · B1/B2", "Cliente B-2086 · B1/B2", "Cliente B-2086 · B1/B2", "Client B-2086 · B1/B2"],
    ["客户 C-3168 · J-1", "Cliente C-3168 · J-1", "Cliente C-3168 · J-1", "Client C-3168 · J-1"],
    ["档案", "Expediente", "Cadastro", "Case"],
    ["客户基础信息", "Datos del cliente", "Dados do cliente", "Client details"],
    ["资料", "Documentos", "Documentos", "Documents"],
    ["上传与识别", "Carga y lectura", "Envio e leitura", "Upload and read"],
    ["整理", "Organizar", "Organizar", "Organise"],
    ["自动结构化", "Estructuración automática", "Estruturação automática", "Automatic structuring"],
    ["字段核查", "Revisión de campos", "Revisão de campos", "Field review"],
    ["来源与置信度", "Fuente y confianza", "Fonte e confiança", "Source and confidence"],
    ["待确认项", "Pendientes", "Pendências", "Items to confirm"],
    ["只问缺失内容", "Solo pregunta lo faltante", "Pergunta apenas o que falta", "Ask only what is missing"],
    ["风险复核", "Revisión de riesgos", "Revisão de riscos", "Risk review"],
    ["冲突集中判断", "Decisiones sobre conflictos", "Decisões sobre conflitos", "Resolve conflicts together"],
    ["DS-160 初稿", "Borrador DS-160", "Rascunho DS-160", "DS-160 draft"],
    ["页面结构映射", "Mapeo por página", "Mapeamento por página", "Page mapping"],
    ["核查清单", "Lista de revisión", "Lista de revisão", "Review checklist"],
    ["提交前检查", "Revisión antes del envío", "Revisão antes do envio", "Pre-submission checks"],
    ["← 上一步", "← Paso anterior", "← Etapa anterior", "← Previous step"],
    ["01 · 档案", "01 · Expediente", "01 · Cadastro", "01 · Case"],
    ["建立客户档案后，签证类型、负责人、时间节点和后续材料始终沿同一案件流转。", "El tipo de visa, el responsable, las fechas y los documentos permanecen en el mismo expediente.", "Tipo de visto, responsável, prazos e documentos permanecem no mesmo caso.", "Visa type, owner, dates and documents stay in the same case."],
    ["3 个进行中案件", "3 expedientes activos", "3 casos em andamento", "3 active cases"],
    ["自动保存 · 刚刚", "Guardado automático · Ahora", "Salvo automaticamente · Agora", "Autosaved · Just now"],
    ["当前工作区", "Área de trabajo", "Área de trabalho", "Current workspace"],
    ["近期建立", "Creado recientemente", "Criado recentemente", "Recently created"],
    ["进行中", "En curso", "Em andamento", "In progress"],
    ["等待补充", "Esperando datos", "Aguardando dados", "Waiting for details"],
    ["等待客户补充", "Esperando al cliente", "Aguardando o cliente", "Waiting for client"],
    ["待确认", "Por confirmar", "A confirmar", "To confirm"],
    ["字段处理中", "Procesando campos", "Processando campos", "Processing fields"],
    ["案件摘要", "Resumen del expediente", "Resumo do caso", "Case summary"],
    ["顾问 01", "Asesor 01", "Consultor 01", "Consultant 01"],
    ["签证路径", "Ruta de visa", "Categoria do visto", "Visa route"],
    ["F-1 学生签证", "Visa de estudiante F-1", "Visto de estudante F-1", "F-1 student visa"],
    ["目标日期", "Fecha objetivo", "Data-alvo", "Target date"],
    ["当前进度", "Avance actual", "Andamento atual", "Current progress"],
    ["02 · 资料", "02 · Documentos", "02 · Documentos", "02 · Documents"],
    ["客户资料", "Documentos del cliente", "Documentos do cliente", "Client documents"],
    ["护照、I-20、身份证和行程资料进入同一案件，系统保留文件、页码和原文证据。", "Pasaporte, I-20, identificación e itinerario permanecen en el mismo expediente, junto con archivo, página y evidencia original.", "Passaporte, I-20, identidade e itinerário permanecem no mesmo caso, junto com arquivo, página e evidência original.", "Passport, I-20, identity and itinerary documents stay in one case with file, page and original evidence."],
    ["3 / 3 已读取", "3 / 3 leídos", "3 / 3 lidos", "3 / 3 read"],
    ["护照 · 12 个字段", "Pasaporte · 12 campos", "Passaporte · 12 campos", "Passport · 12 fields"],
    ["DeepSeek 结构化", "Estructurado por DeepSeek", "Estruturado pelo DeepSeek", "Structured by DeepSeek"],
    ["学校与 SEVIS · 17 个字段", "Escuela y SEVIS · 17 campos", "Instituição e SEVIS · 17 campos", "School and SEVIS · 17 fields"],
    ["版面识别", "Lectura de diseño", "Leitura de layout", "Layout recognition"],
    ["身份与地址 · 8 个字段", "Identidad y domicilio · 8 campos", "Identidade e endereço · 8 campos", "Identity and address · 8 fields"],
    ["图像识别", "Lectura de imagen", "Leitura de imagem", "Image recognition"],
    ["已提取字段", "Campos extraídos", "Campos extraídos", "Fields extracted"],
    ["识别失败", "Lecturas fallidas", "Falhas de leitura", "Read failures"],
    ["重复材料", "Duplicados", "Duplicados", "Duplicates"],
    ["最新处理", "Último proceso", "Último processamento", "Latest run"],
    ["刚刚", "Ahora", "Agora", "Just now"],
    ["03 · 整理", "03 · Organización", "03 · Organização", "03 · Organisation"],
    ["材料整理", "Organización de documentos", "Organização de documentos", "Document organisation"],
    ["DeepSeek 理解并翻译材料内容，RPA 把结果合并到统一的 DS-160 字段体系。", "DeepSeek comprende y traduce los documentos; RPA integra el resultado en los campos del DS-160.", "O DeepSeek entende e traduz os documentos; o RPA integra o resultado aos campos do DS-160.", "DeepSeek understands and translates documents; RPA merges the result into the DS-160 field structure."],
    ["自动整理中", "Organizando automáticamente", "Organizando automaticamente", "Organising automatically"],
    ["身份与护照", "Identidad y pasaporte", "Identidade e passaporte", "Identity and passport"],
    ["12 个字段", "12 campos", "12 campos", "12 fields"],
    ["来源证据已绑定", "Evidencia vinculada", "Evidência vinculada", "Evidence linked"],
    ["学校与 SEVIS", "Escuela y SEVIS", "Instituição e SEVIS", "School and SEVIS"],
    ["17 个字段", "17 campos", "17 campos", "17 fields"],
    ["英文原文已保留", "Original en inglés conservado", "Original em inglês preservado", "English original retained"],
    ["旅行与联系方式", "Viaje y contacto", "Viagem e contato", "Travel and contact"],
    ["13 个字段", "13 campos", "13 campos", "13 fields"],
    ["正在合并", "Integrando", "Integrando", "Merging"],
    ["处理中", "Procesando", "Processando", "Processing"],
    ["材料理解", "Comprensión documental", "Compreensão documental", "Document understanding"],
    ["重复操作", "Tareas repetitivas", "Tarefas repetitivas", "Repetitive tasks"],
    ["页面观察", "Observación de página", "Observação da página", "Page observation"],
    ["异常处理", "Manejo de excepciones", "Tratamento de exceções", "Exception handling"],
    ["自动暂停", "Pausa automática", "Pausa automática", "Automatic pause"],
    ["04 · 字段核查", "04 · Revisión de campos", "04 · Revisão de campos", "04 · Field review"],
    ["只把有冲突、低置信度或缺少来源的字段交给顾问处理，已确认内容保持安静。", "Solo los campos con conflictos, baja confianza o sin fuente pasan al asesor; lo confirmado no genera ruido.", "Apenas campos com conflitos, baixa confiança ou sem fonte vão ao consultor; o que foi confirmado não gera ruído.", "Only conflicting, low-confidence or unsupported fields go to the consultant; confirmed data stays quiet."],
    ["3 项需要处理", "3 elementos por revisar", "3 itens para revisar", "3 items need review"],
    ["预计抵达日期", "Fecha prevista de llegada", "Data prevista de chegada", "Intended arrival date"],
    ["行程单 / 客户补充", "Itinerario / Cliente", "Itinerário / Cliente", "Itinerary / Client"],
    ["信息冲突", "Conflicto de datos", "Conflito de dados", "Data conflict"],
    ["曾用名拼写", "Ortografía de otro nombre", "Grafia de outro nome", "Other-name spelling"],
    ["护照第 1 页", "Pasaporte, página 1", "Passaporte, página 1", "Passport, page 1"],
    ["需要核对", "Requiere revisión", "Requer revisão", "Needs review"],
    ["美国联系人", "Contacto en EE. UU.", "Contato nos EUA", "U.S. contact"],
    ["I-20 第 1 页", "I-20, página 1", "I-20, página 1", "I-20, page 1"],
    ["学校联系人", "Contacto de la escuela", "Contato da instituição", "School contact"],
    ["整理字段", "Campos organizados", "Campos organizados", "Organised fields"],
    ["无需处理", "Sin acción", "Sem ação", "No action needed"],
    ["待核查", "Por revisar", "A revisar", "To review"],
    ["05 · 待确认项", "05 · Pendientes", "05 · Pendências", "05 · Items to confirm"],
    ["系统只生成真正缺失或需要澄清的问题，客户已经提供过的信息不会重复询问。", "El sistema pregunta solo por datos realmente faltantes o ambiguos y no repite lo que el cliente ya entregó.", "O sistema pergunta apenas sobre dados realmente ausentes ou ambíguos e não repete o que o cliente já informou.", "The system asks only about genuinely missing or unclear details and does not repeat what the client has already provided."],
    ["2 项等待回答", "2 respuestas pendientes", "2 respostas pendentes", "2 answers pending"],
    ["过去五年出境记录", "Viajes internacionales de los últimos cinco años", "Viagens internacionais dos últimos cinco anos", "International travel in the last five years"],
    ["材料未提供", "No aparece en documentos", "Não consta nos documentos", "Not in documents"],
    ["等待客户回答", "Esperando respuesta", "Aguardando resposta", "Waiting for client answer"],
    ["待补充", "Falta completar", "A completar", "Missing"],
    ["美国联系人电话", "Teléfono del contacto en EE. UU.", "Telefone do contato nos EUA", "U.S. contact phone"],
    ["I-20 缺少号码", "Falta el número en el I-20", "Número ausente no I-20", "Number missing from I-20"],
    ["已生成问题", "Pregunta creada", "Pergunta criada", "Question created"],
    ["预计停留时间", "Duración prevista", "Tempo previsto de estadia", "Intended length of stay"],
    ["行程信息", "Datos del viaje", "Dados da viagem", "Travel details"],
    ["120 天", "120 días", "120 dias", "120 days"],
    ["需要补充", "Por completar", "A completar", "Needs details"],
    ["客户已回答", "Respondido por el cliente", "Respondido pelo cliente", "Answered by client"],
    ["无需再问", "No volver a preguntar", "Não perguntar novamente", "No need to ask"],
    ["链接有效期", "Vigencia del enlace", "Validade do link", "Link validity"],
    ["48 小时", "48 horas", "48 horas", "48 hours"],
    ["06 · 风险复核", "06 · Revisión de riesgos", "06 · Revisão de riscos", "06 · Risk review"],
    ["冲突、敏感背景和异常字段提前归集；需要专业判断的内容始终留给顾问。", "Los conflictos, datos sensibles y campos anómalos se agrupan antes; las decisiones profesionales quedan siempre con el asesor.", "Conflitos, dados sensíveis e campos anormais são agrupados antes; decisões profissionais ficam sempre com o consultor.", "Conflicts, sensitive history and unusual fields are grouped early; professional decisions always remain with the consultant."],
    ["顾问复核", "Revisión del asesor", "Revisão do consultor", "Consultant review"],
    ["旅行日期不一致", "Fechas de viaje distintas", "Datas de viagem divergentes", "Travel dates differ"],
    ["行程单 / 补充答案", "Itinerario / Respuesta", "Itinerário / Resposta", "Itinerary / Answer"],
    ["相差 2 天", "Diferencia de 2 días", "Diferença de 2 dias", "2-day difference"],
    ["需要判断", "Requiere criterio", "Requer avaliação", "Needs judgement"],
    ["安全背景问题", "Preguntas de seguridad", "Perguntas de segurança", "Security questions"],
    ["客户本人陈述", "Declaración del cliente", "Declaração do cliente", "Client statement"],
    ["必须逐项确认", "Confirmación punto por punto", "Confirmação item a item", "Confirm each item"],
    ["敏感内容", "Contenido sensible", "Conteúdo sensível", "Sensitive content"],
    ["曾用名英文拼写", "Otro nombre en inglés", "Outro nome em inglês", "Other name in English"],
    ["护照 / 旧材料", "Pasaporte / Documento anterior", "Passaporte / Documento anterior", "Passport / Older document"],
    ["差异已解释", "Diferencia explicada", "Diferença explicada", "Difference explained"],
    ["复核项目", "Elementos de revisión", "Itens de revisão", "Review items"],
    ["日期冲突", "Conflictos de fecha", "Conflitos de data", "Date conflicts"],
    ["敏感问题", "Preguntas sensibles", "Perguntas sensíveis", "Sensitive questions"],
    ["稳定字段", "Campos estables", "Campos estáveis", "Stable fields"],
    ["初稿预览", "Vista del borrador", "Visão do rascunho", "Draft preview"],
    ["Computer Use 执行台", "Panel de Computer Use", "Painel do Computer Use", "Computer Use console"],
    ["B1/B2 访问签证", "Visa de visitante B1/B2", "Visto de visitante B1/B2", "B1/B2 visitor visa"],
    ["DS-160 初稿预览", "Vista del borrador DS-160", "Visão do rascunho DS-160", "DS-160 draft preview"],
    ["按 DS-160 模块展示可复核的填写初稿。敏感背景问题仅显示提醒，不自动代填。", "El borrador se presenta por secciones del DS-160 para revisión. Las preguntas sensibles solo se señalan y no se contestan automáticamente.", "O rascunho é apresentado por seções do DS-160 para revisão. Perguntas sensíveis apenas recebem alertas e não são preenchidas automaticamente.", "The draft is shown by DS-160 section for review. Sensitive background questions are flagged and never answered automatically."],
    ["客户 A-1024　负责人：顾问 01　初稿已生成　B1/B2 访问签证　更新于 2026年8月16日 14:30", "Cliente A-1024 · Asesor 01 · Borrador generado · Visa B1/B2 · Actualizado 16 ago 2026, 14:30", "Cliente A-1024 · Consultor 01 · Rascunho gerado · Visto B1/B2 · Atualizado 16 ago 2026, 14:30", "Client A-1024 · Consultant 01 · Draft generated · B1/B2 visa · Updated 16 Aug 2026, 14:30"],
    ["初稿仅供中介人员核查。Computer Use 可以在可见 Chrome 中辅助写入 CEAC，但验证码、敏感背景判断、电子签名和最终提交必须由人工完成。", "El borrador es para revisión de la agencia. Computer Use puede asistir la escritura en CEAC dentro de Chrome visible, pero CAPTCHA, decisiones sensibles, firma y envío final son manuales.", "O rascunho é para revisão da agência. O Computer Use pode auxiliar a inserção no CEAC em Chrome visível, mas CAPTCHA, decisões sensíveis, assinatura e envio final são manuais.", "The draft is for agency review. Computer Use can assist entry into CEAC in visible Chrome, but CAPTCHA, sensitive decisions, signature and final submission remain manual."],
    ["申请信息", "Datos de la solicitud", "Dados da solicitação", "Application details"],
    ["8 个模块", "8 secciones", "8 seções", "8 sections"],
    ["计划申请的使领馆国家 / 地区", "País o región del consulado", "País ou região do consulado", "Consular country or region"],
    ["签证类型 / 访问目的", "Tipo de visa / Motivo del viaje", "Tipo de visto / Motivo da viagem", "Visa type / Purpose of travel"],
    ["预计抵达美国日期", "Fecha prevista de llegada a EE. UU.", "Data prevista de chegada aos EUA", "Intended U.S. arrival date"],
    ["基础信息", "Datos personales", "Dados pessoais", "Personal details"],
    ["来源可追溯", "Fuente rastreable", "Fonte rastreável", "Traceable source"],
    ["姓（Surname）", "Apellidos (Surname)", "Sobrenome (Surname)", "Surname"],
    ["名（Given Names）", "Nombres (Given Names)", "Nomes (Given Names)", "Given names"],
    ["出生日期", "Fecha de nacimiento", "Data de nascimento", "Date of birth"],
    ["性别", "Sexo", "Sexo", "Sex"],
    ["查看 Computer Use 逐页填写界面 →", "Ver el llenado paso a paso en Computer Use →", "Ver o preenchimento passo a passo no Computer Use →", "View page-by-page entry in Computer Use →"],
    ["08 · 核查清单", "08 · Lista de revisión", "08 · Lista de revisão", "08 · Review checklist"],
    ["在离开系统前集中核对来源、未决问题与人工操作边界，形成可导出的审计清单。", "Antes de salir, revisa fuentes, pendientes y pasos manuales, y genera una lista auditable para exportar.", "Antes de sair, revise fontes, pendências e etapas manuais e gere uma lista auditável para exportação.", "Before leaving, review sources, open questions and manual boundaries, then export an auditable checklist."],
    ["24 / 28 已通过", "24 / 28 aprobados", "24 / 28 aprovados", "24 / 28 passed"],
    ["提交前核查", "Revisión previa al envío", "Revisão antes do envio", "Pre-submission review"],
    ["身份与护照一致性", "Coincidencia de identidad y pasaporte", "Consistência entre identidade e passaporte", "Identity and passport consistency"],
    ["12 项检查", "12 comprobaciones", "12 verificações", "12 checks"],
    ["全部匹配", "Todo coincide", "Tudo confere", "All matched"],
    ["通过", "Aprobado", "Aprovado", "Passed"],
    ["旅行信息完整性", "Integridad de datos de viaje", "Completude dos dados de viagem", "Travel information completeness"],
    ["8 项检查", "8 comprobaciones", "8 verificações", "8 checks"],
    ["2 项待确认", "2 por confirmar", "2 a confirmar", "2 to confirm"],
    ["核查中", "En revisión", "Em revisão", "Under review"],
    ["安全背景逐项确认", "Confirmación de seguridad punto por punto", "Confirmação de segurança item a item", "Item-by-item security confirmation"],
    ["顾问负责", "Responsable: asesor", "Responsável: consultor", "Consultant responsible"],
    ["尚未最终确认", "Aún sin confirmación final", "Ainda sem confirmação final", "Not finally confirmed"],
    ["人工确认", "Confirmación manual", "Confirmação manual", "Manual confirmation"],
    ["检查项目", "Comprobaciones", "Verificações", "Checks"],
    ["已经通过", "Aprobadas", "Aprovadas", "Passed"],
    ["需要确认", "Por confirmar", "A confirmar", "Need confirmation"],
    ["人工操作", "Pasos manuales", "Etapas manuais", "Manual steps"],
    ["← DS-160 初稿", "← Borrador DS-160", "← Rascunho DS-160", "← DS-160 draft"],
    ["系统级可见操作 · 无需 Chrome 扩展", "Operación visible del sistema · Sin extensión de Chrome", "Operação visível do sistema · Sem extensão do Chrome", "Visible system operation · No Chrome extension"],
    ["任务已准备", "Tarea preparada", "Tarefa preparada", "Task prepared"],
    ["Computer Use 执行通道已就绪", "Canal de Computer Use listo", "Canal do Computer Use pronto", "Computer Use channel is ready"],
    ["WestoryVisa 只准备短时字段任务并打开 CEAC；实际点击、输入、下拉选择与页面复读由 Codex Desktop 的 Computer Use 完成。", "WestoryVisa prepara una tarea temporal y abre CEAC; Computer Use de Codex Desktop realiza los clics, la escritura, las selecciones y la verificación visible.", "A WestoryVisa prepara uma tarefa temporária e abre o CEAC; o Computer Use do Codex Desktop realiza cliques, digitação, seleções e verificação visível.", "WestoryVisa prepares a short-lived task and opens CEAC; Codex Desktop Computer Use handles visible clicks, typing, selections and page read-back."],
    ["当前客户的逐页字段计划", "Plan de campos por página", "Plano de campos por página", "Page-by-page field plan"],
    ["60 分钟本机任务", "Tarea local de 60 minutos", "Tarefa local de 60 minutos", "60-minute local task"],
    ["生成当前档案白名单", "Crear lista permitida del expediente", "Criar lista permitida do caso", "Build the case allowlist"],
    ["准备任务", "Preparar tarea", "Preparar tarefa", "Prepare task"],
    ["打开官方起始页", "Abrir página oficial", "Abrir página oficial", "Open official start page"],
    ["人工进入", "Entrada manual", "Entrada manual", "Manual entry"],
    ["验证码与初始步骤", "CAPTCHA y pasos iniciales", "CAPTCHA e etapas iniciais", "CAPTCHA and initial steps"],
    ["可见填写", "Llenado visible", "Preenchimento visível", "Visible entry"],
    ["逐项复读并受控 Next", "Verificar cada valor y controlar Next", "Verificar cada valor e controlar Next", "Read back each value and control Next"],
    ["人工核查", "Revisión manual", "Revisão manual", "Manual review"],
    ["敏感或未映射页暂停", "Pausa en páginas sensibles o no mapeadas", "Pausa em páginas sensíveis ou não mapeadas", "Pause on sensitive or unmapped pages"],
    ["可交接信息预览", "Vista de datos transferibles", "Visão dos dados transferíveis", "Handoff data preview"],
    ["24 项预览", "24 elementos", "24 itens", "24 items"],
    ["基础信息 · 姓（Surname）", "Datos personales · Apellidos", "Dados pessoais · Sobrenome", "Personal · Surname"],
    ["基础信息 · 名（Given Names）", "Datos personales · Nombres", "Dados pessoais · Nomes", "Personal · Given names"],
    ["基础信息 · 出生日期", "Datos personales · Fecha de nacimiento", "Dados pessoais · Data de nascimento", "Personal · Date of birth"],
    ["护照信息 · 护照号码", "Pasaporte · Número", "Passaporte · Número", "Passport · Number"],
    ["护照信息 · 护照有效期至", "Pasaporte · Vencimiento", "Passaporte · Validade", "Passport · Expiry"],
    ["旅行信息 · 签证类型 / 访问目的", "Viaje · Tipo de visa / Motivo", "Viagem · Tipo de visto / Motivo", "Travel · Visa type / Purpose"],
    ["旅行信息 · 预计抵达日期", "Viaje · Fecha de llegada", "Viagem · Data de chegada", "Travel · Arrival date"],
    ["美国联系人 · 地址", "Contacto en EE. UU. · Domicilio", "Contato nos EUA · Endereço", "U.S. contact · Address"],
    ["当前执行状态", "Estado actual", "Status atual", "Current execution status"],
    ["字段进度", "Avance de campos", "Andamento dos campos", "Field progress"],
    ["准备好后从这里打开 CEAC", "Abre CEAC aquí cuando la tarea esté lista", "Abra o CEAC aqui quando a tarefa estiver pronta", "Open CEAC here when the task is ready"],
    ["当前 Chrome", "Chrome actual", "Chrome atual", "Current Chrome"],
    ["节奏", "Ritmo", "Ritmo", "Pace"],
    ["核验后", "Tras verificar", "Após verificar", "After verification"],
    ["目标网站", "Sitio objetivo", "Site de destino", "Target site"],
    ["已记录页面路径", "Rutas registradas", "Rotas registradas", "Recorded page paths"],
    ["已映射 0 · 当前尚未捕获表格路径", "0 mapeadas · Aún no se detecta la ruta del formulario", "0 mapeadas · A rota do formulário ainda não foi detectada", "0 mapped · Form path not captured yet"],
    ["尚未准备本机任务", "Tarea local aún no preparada", "Tarefa local ainda não preparada", "Local task not prepared"],
    ["一次性令牌只保存在当前页面内存中，任务关闭后服务器会擦除字段值。", "El token temporal solo vive en la memoria de esta página; el servidor borra los valores al cerrar la tarea.", "O token temporário existe apenas na memória desta página; o servidor apaga os valores ao fechar a tarefa.", "The one-time token lives only in this page's memory; the server erases field values when the task closes."],
    ["普通页面连续填写", "Llenado continuo en páginas normales", "Preenchimento contínuo em páginas comuns", "Continuous entry on standard pages"],
    ["全部复读无误后才点击 Next", "Hacer clic en Next solo después de verificar todo", "Clicar em Next apenas após verificar tudo", "Click Next only after every value is verified"],
    ["准备任务并打开 CEAC", "Preparar tarea y abrir CEAC", "Preparar tarefa e abrir CEAC", "Prepare task and open CEAC"],
    ["自动化服务运行中", "Automatización activa", "Automação ativa", "Automation running"],
    ["产品预览", "Vista del producto", "Visão do produto", "Product preview"],
    ["页面数据已经过脱敏处理。", "Los datos mostrados están anonimizados.", "Os dados exibidos foram anonimizados.", "Displayed data is anonymised."],
    ["暂停自动播放", "Pausar reproducción", "Pausar reprodução", "Pause autoplay"],
    ["继续自动播放", "Reanudar reproducción", "Retomar reprodução", "Resume autoplay"],
    ["产品细节", "Detalles del producto", "Detalhes do produto", "Product details"],
    ["再了解一下 WestoryVisa", "Conoce mejor WestoryVisa", "Conheça melhor a WestoryVisa", "Take a closer look at WestoryVisa"],
    ["字段证据", "Evidencia de campo", "Evidência do campo", "Field evidence"],
    ["不是猜出来，", "No se adivina,", "Não é suposição,", "Not guessed,"],
    ["是找得到。", "se puede comprobar.", "dá para comprovar.", "fully traceable."],
    ["来源已绑定", "Fuente vinculada", "Fonte vinculada", "Source linked"],
    ["文件、页码和原文证据，始终跟着字段一起保留。", "Cada campo conserva su archivo, página y texto original.", "Cada campo mantém o arquivo, a página e o texto original.", "Every field keeps its file, page and original evidence."],
    ["团队交接", "Entrega entre equipo", "Passagem entre equipe", "Team handover"],
    ["换个人接手，", "Cambia de responsable,", "Troque o responsável,", "Hand it over,"],
    ["不用从头来。", "sin empezar de cero.", "sem recomeçar.", "without starting again."],
    ["林", "L", "L", "L"],
    ["周", "Z", "Z", "Z"],
    ["陈", "C", "C", "C"],
    ["进度、待确认原因和人工修改，都留在同一个案件中。", "El avance, los pendientes y los cambios manuales quedan en el mismo expediente.", "O andamento, as pendências e as alterações manuais ficam no mesmo caso.", "Progress, open questions and manual edits stay in the same case."],
    ["二审复核", "Segunda revisión", "Segunda revisão", "Second review"],
    ["改了哪里，", "Ve cada cambio,", "Veja cada alteração,", "See every change,"],
    ["一眼看清。", "al instante.", "de imediato.", "at a glance."],
    ["系统整理", "Resultado del sistema", "Resultado do sistema", "System output"],
    ["人工更正", "Corrección manual", "Correção manual", "Manual correction"],
    ["2 处变化", "2 cambios", "2 alterações", "2 changes"],
    ["系统结果与人工更正分开记录，复核只关注真正的变化。", "El sistema y las correcciones manuales se registran por separado para revisar solo los cambios reales.", "O resultado do sistema e as correções manuais ficam separados para revisar apenas as mudanças reais.", "System output and manual corrections are separate, so reviewers focus only on real changes."],
    ["客户补充", "Datos del cliente", "Complemento do cliente", "Client follow-up"],
    ["只问缺的，", "Pregunta solo lo que falta,", "Pergunta apenas o que falta,", "Ask only for what is missing,"],
    ["不问已有的。", "no lo que ya tienes.", "não o que já existe.", "not what is already known."],
    ["✓ 护照信息", "✓ Pasaporte", "✓ Passaporte", "✓ Passport details"],
    ["✓ 学校信息", "✓ Escuela", "✓ Instituição de ensino", "✓ School details"],
    ["护照信息", "Datos del pasaporte", "Dados do passaporte", "Passport details"],
    ["学校信息", "Datos de la escuela", "Dados da instituição de ensino", "School details"],
    ["美国联系人电话？", "¿Teléfono del contacto en EE. UU.?", "Telefone do contato nos EUA?", "U.S. contact phone?"],
    ["1 个待补充", "1 dato pendiente", "1 dado pendente", "1 item missing"],
    ["已有资料自动跳过，只把缺失或冲突的问题发给客户。", "Omite lo ya disponible y pregunta al cliente solo por datos faltantes o contradictorios.", "Ignora o que já existe e pergunta ao cliente apenas sobre dados ausentes ou conflitantes.", "Skip what is already known and ask clients only about missing or conflicting details."],
    ["复杂流程，也能清楚推进。", "Un proceso complejo, siempre claro.", "Um processo complexo, sempre claro.", "Keep a complex process clear."],
    ["材料、来源、进度和待确认项始终保持清楚，团队交接不必从头开始。", "Documentos, fuentes, avance y pendientes quedan claros para que el equipo continúe sin reiniciar.", "Documentos, fontes, andamento e pendências ficam claros para a equipe continuar sem recomeçar.", "Documents, sources, progress and open items stay clear so the next person can continue without restarting."],
    ["查看完整工作流", "Ver flujo completo", "Ver fluxo completo", "View full workflow"],
    ["WestoryVisa 是面向签证顾问及机构客户的软件辅助工具，并非美国政府、美国国务院、任何使领馆或签证签发机构的官方网站或授权代表。WestoryVisa 不提供法律意见，也不保证签证申请获批。", "WestoryVisa es una herramienta para asesores y agencias de visas. No es un sitio oficial ni representante autorizado del Gobierno de Estados Unidos, el Departamento de Estado, una embajada, consulado o autoridad de visas. No ofrece asesoría legal ni garantiza la aprobación.", "WestoryVisa é uma ferramenta para consultores e agências de vistos. Não é site oficial nem representante autorizado do Governo dos Estados Unidos, Departamento de Estado, embaixada, consulado ou autoridade de vistos. Não oferece assessoria jurídica nem garante aprovação.", "WestoryVisa is a software tool for visa consultants and agencies. It is not an official website or authorised representative of the U.S. Government, Department of State, any embassy, consulate or visa authority. It does not provide legal advice or guarantee approval."],
    ["WestoryVisa 可辅助进行材料识别、翻译、字段整理、信息核查、DS‑160 初稿生成及可见页面填写。所有自动生成或填写的内容均须由申请人或其授权顾问人工核对。申请人应对提交信息的真实性、准确性、完整性和时效性承担责任。", "WestoryVisa puede asistir en lectura, traducción, organización y revisión de datos, generación del borrador DS-160 y llenado de páginas visibles. El solicitante o su asesor autorizado debe revisar todo contenido generado o escrito, y el solicitante responde por su veracidad, exactitud, integridad y vigencia.", "WestoryVisa pode auxiliar na leitura, tradução, organização e revisão de dados, geração do rascunho DS-160 e preenchimento de páginas visíveis. O solicitante ou consultor autorizado deve revisar todo conteúdo gerado ou inserido, e o solicitante responde por sua veracidade, exatidão, integridade e atualidade.", "WestoryVisa can assist with document reading, translation, field organisation, data review, DS-160 draft generation and visible-page entry. The applicant or authorised consultant must review all generated or entered content, and the applicant remains responsible for its truth, accuracy, completeness and currency."],
    ["验证码、账户凭证、拒签或移民历史判断、安全与背景问题、电子签名、法律声明、政府费用支付及最终提交不由本服务自动完成，必须由申请人或经授权的顾问人工处理。美国国务院也要求 DS‑160 信息准确、完整，并要求第三方协助者在相应页面如实披露，最终签署应由申请人本人完成。", "CAPTCHA, credenciales, evaluaciones sobre rechazos o historial migratorio, preguntas de seguridad, firma electrónica, declaraciones legales, pagos oficiales y envío final no se automatizan. Deben ser atendidos por el solicitante o un asesor autorizado. El Departamento de Estado exige información DS-160 exacta y completa, la declaración de ayuda de terceros y la firma final del solicitante.", "CAPTCHA, credenciais, avaliações sobre recusas ou histórico migratório, perguntas de segurança, assinatura eletrônica, declarações legais, pagamentos oficiais e envio final não são automatizados. Devem ser tratados pelo solicitante ou consultor autorizado. O Departamento de Estado exige dados DS-160 exatos e completos, declaração de ajuda de terceiros e assinatura final do solicitante.", "CAPTCHA, credentials, decisions about refusals or immigration history, security questions, electronic signature, legal declarations, government fees and final submission are not automated. They must be handled by the applicant or an authorised consultant. The Department of State requires accurate and complete DS-160 information, disclosure of third-party assistance and the applicant's final signature."],
    ["查看美国国务院 DS‑160 填写说明", "Ver instrucciones oficiales del DS-160", "Ver orientações oficiais do DS-160", "View official DS-160 guidance"],
    ["页面所示处理时间为内部模拟环境下的参考结果，不包含验证码、人工复核、电子签名、付款及最终提交。实际处理时间可能因材料完整程度、目标网站状态、网络环境及第三方服务可用性而有所不同。", "Los tiempos mostrados provienen de pruebas internas y no incluyen CAPTCHA, revisión humana, firma, pago ni envío final. El tiempo real depende de los documentos, el sitio, la red y los servicios de terceros.", "Os tempos exibidos vêm de testes internos e não incluem CAPTCHA, revisão humana, assinatura, pagamento nem envio final. O tempo real depende dos documentos, do site, da rede e de serviços de terceiros.", "Shown processing times come from internal tests and exclude CAPTCHA, human review, signature, payment and final submission. Actual time depends on documents, site status, network conditions and third-party services."],
    ["为提供服务，WestoryVisa 可能处理机构账户信息，以及申请人主动提供或由获授权机构上传的身份、护照、联系方式、旅行、教育、工作和签证申请材料。相关信息仅用于账户管理、材料整理、字段核查、服务安全及履行合同，不用于广告画像或与签证服务无关的用途。详情请参阅", "Para prestar el servicio, WestoryVisa puede tratar datos de la cuenta de la agencia y documentos de identidad, pasaporte, contacto, viaje, educación, trabajo y solicitud proporcionados por el solicitante o una agencia autorizada. Se usan solo para administrar cuentas, organizar y revisar datos, proteger el servicio y cumplir el contrato, no para publicidad. Consulta el", "Para prestar o serviço, a WestoryVisa pode tratar dados da conta da agência e documentos de identidade, passaporte, contato, viagem, educação, trabalho e solicitação fornecidos pelo solicitante ou agência autorizada. São usados apenas para administrar contas, organizar e revisar dados, proteger o serviço e cumprir o contrato, não para publicidade. Consulte a", "To provide the service, WestoryVisa may process agency account data and identity, passport, contact, travel, education, employment and application documents provided by the applicant or an authorised agency. Data is used only for account management, organisation and review, service security and contract performance, not advertising. See the"],
    ["预约产品演示时，我们会保存访客主动填写的姓名、电话、邮箱和备注，用于联系、安排演示及跟进服务；系统会将预约摘要发送至 WestoryVisa 指定的业务收件邮箱。", "Al solicitar una demostración guardamos el nombre, teléfono, correo y notas enviados para contactar, coordinar y dar seguimiento; un resumen se envía al correo comercial designado de WestoryVisa.", "Ao solicitar uma demonstração, guardamos nome, telefone, e-mail e observações enviados para contato, agendamento e acompanhamento; um resumo é enviado ao e-mail comercial designado da WestoryVisa.", "When a demo is requested, we store the submitted name, phone, email and notes for contact, scheduling and follow-up; a summary is sent to WestoryVisa's designated business inbox."],
    ["材料可能由云托管、文档识别、翻译、人工智能、邮件或技术支持服务商按照约定进行必要处理，部分处理可能涉及跨境传输。机构客户上传材料前，应依法向申请人履行告知义务，并取得处理敏感个人信息及跨境提供所需的授权或单独同意。", "Proveedores de nube, lectura documental, traducción, inteligencia artificial, correo o soporte pueden tratar los datos según contrato, incluso entre países. Antes de cargar documentos, la agencia debe informar al solicitante y obtener las autorizaciones exigidas para datos sensibles y transferencias internacionales.", "Provedores de nuvem, leitura documental, tradução, inteligência artificial, e-mail ou suporte podem tratar os dados conforme contrato, inclusive entre países. Antes do envio, a agência deve informar o solicitante e obter as autorizações exigidas para dados sensíveis e transferências internacionais.", "Cloud hosting, document reading, translation, AI, email or support providers may process data under contract, including across borders. Before uploading documents, the agency must inform the applicant and obtain any required authorisation for sensitive data and international transfers."],
    ["我们采用传输加密、权限隔离、访问日志、最小权限及其他合理安全措施保护数据，但任何网络系统均无法保证绝对安全。用户应妥善保管登录凭证、限制团队权限，并及时删除不再需要的导出文件。", "Aplicamos cifrado en tránsito, aislamiento de permisos, registros de acceso y privilegio mínimo, pero ningún sistema es totalmente seguro. El usuario debe proteger sus credenciales, limitar permisos del equipo y borrar exportaciones que ya no necesite.", "Aplicamos criptografia em trânsito, isolamento de permissões, registros de acesso e privilégio mínimo, mas nenhum sistema é totalmente seguro. O usuário deve proteger credenciais, limitar permissões da equipe e excluir exportações desnecessárias.", "We use encryption in transit, permission isolation, access logs and least privilege, but no network system is completely secure. Users must protect credentials, limit team permissions and delete exports that are no longer needed."],
    ["月度和年度会员均为一次性购买的固定服务期限，不自动续费。功能、价格、币种、税费、实际扣款金额及退款条件，以购买页面和", "Los planes mensuales y anuales son compras de duración fija y no se renuevan automáticamente. Funciones, precio, moneda, impuestos, cobro y reembolsos se rigen por la página de compra y la", "Os planos mensais e anuais são compras de prazo fixo e não se renovam automaticamente. Recursos, preço, moeda, impostos, cobrança e reembolsos seguem a página de compra e a", "Monthly and annual plans are fixed-term purchases and do not renew automatically. Features, price, currency, taxes, charges and refunds are governed by the purchase page and the"],
    ["为准。", ".", ".", "."],
    ["。", ".", ".", "."],
    ["某些功能可能因政府网站、浏览器、地区、语言或第三方服务调整而暂时不可用。产品功能和界面可能随版本更新发生变化。", "Algunas funciones pueden quedar temporalmente no disponibles por cambios en sitios gubernamentales, navegadores, regiones, idiomas o servicios de terceros. El producto y su interfaz pueden cambiar con nuevas versiones.", "Alguns recursos podem ficar temporariamente indisponíveis por mudanças em sites governamentais, navegadores, regiões, idiomas ou serviços de terceiros. O produto e a interface podem mudar com novas versões.", "Some features may be temporarily unavailable due to changes in government sites, browsers, regions, languages or third-party services. Product features and interface may change between releases."],
    ["服务条款", "Términos", "Termos", "Terms"],
    ["隐私政策", "Aviso de privacidad", "Política de privacidade", "Privacy policy"],
    ["退款政策", "Política de reembolso", "Política de reembolso", "Refund policy"],
    ["退款与取消政策", "política de reembolso y cancelación", "política de reembolso e cancelamento", "refund and cancellation policy"],
    ["联系我们", "Contacto", "Fale conosco", "Contact us"],
    ["验证码、电子签名和最终提交保留人工控制", "CAPTCHA, firma electrónica y envío final quedan bajo control humano", "CAPTCHA, assinatura eletrônica e envio final permanecem sob controle humano", "CAPTCHA, electronic signature and final submission remain under human control"],
    ["留下联系方式，我们会尽快与你确认演示时间。", "Déjanos tus datos y coordinaremos la demostración contigo.", "Deixe seus dados e combinaremos o horário da demonstração.", "Leave your contact details and we will arrange a demo time."],
    ["名字 *", "Nombre *", "Nome *", "Name *"],
    ["电话 *", "Teléfono *", "Telefone *", "Phone *"],
    ["邮箱 *", "Correo electrónico *", "E-mail *", "Email *"],
    ["其他备注", "Notas", "Observações", "Notes"],
    ["怎么称呼你", "Tu nombre", "Como podemos chamar você", "Your name"],
    ["可以填写团队情况、希望了解的功能或方便联系的时间", "Cuéntanos sobre tu equipo, las funciones que te interesan o el mejor horario para contactarte.", "Conte sobre sua equipe, os recursos de interesse ou o melhor horário para contato.", "Tell us about your team, features of interest or a convenient contact time."],
    ["提交预约", "Enviar solicitud", "Enviar solicitação", "Submit request"],
    ["提交后，预约信息会发送给 WestoryVisa 团队。", "La solicitud se enviará al equipo de WestoryVisa.", "A solicitação será enviada à equipe da WestoryVisa.", "Your request will be sent to the WestoryVisa team."],
    ["预约已收到", "Solicitud recibida", "Solicitação recebida", "Request received"],
    ["谢谢你的信息。WestoryVisa 团队会尽快与你联系。", "Gracias. El equipo de WestoryVisa se pondrá en contacto pronto.", "Obrigado. A equipe da WestoryVisa entrará em contato em breve.", "Thank you. The WestoryVisa team will contact you soon."],
    ["完成", "Listo", "Concluir", "Done"],

    // Agency sign-in and the shared China workflow shell.
    ["WestoryVisa｜签证中介填写辅助工具", "WestoryVisa | Sistema DS-160 para agencias", "WestoryVisa | Sistema DS-160 para agências", "WestoryVisa | DS-160 agency workspace"],
    ["面向文案老师和签证顾问的 DS-160 工作台", "Sistema DS-160 para asesores y agencias de visas", "Sistema DS-160 para consultores e agências de vistos", "DS-160 workspace for visa consultants and agencies"],
    ["高效核查", "Revisión eficiente", "Revisão eficiente", "Efficient review"],
    ["按 DS-160 真实结构整理客户资料，辅助生成可核查的填写初稿。中介人员负责专业判断与最终确认，系统负责减少重复输入、字段遗漏和资料来回确认。", "Organiza los datos con la estructura real del DS-160 y genera un borrador revisable. Tu agencia conserva el criterio profesional y la confirmación final; el sistema reduce capturas repetidas, omisiones y vueltas con el cliente.", "Organize os dados conforme a estrutura real do DS-160 e gere um rascunho revisável. Sua agência mantém o julgamento profissional e a confirmação final; o sistema reduz digitação repetida, omissões e retrabalho com o cliente.", "Organise client data around the real DS-160 structure and generate a reviewable draft. Your agency keeps professional judgement and final confirmation; the system reduces repeated entry, omissions and client follow-up."],
    ["账号登录", "Iniciar sesión", "Entrar", "Sign in"],
    ["注册机构账号", "Registrar agencia", "Cadastrar agência", "Register agency"],
    ["机构 / 团队名称", "Nombre de la agencia", "Nome da agência", "Agency name"],
    ["联系人姓名", "Nombre del contacto", "Nome do contato", "Contact name"],
    ["手机号", "Teléfono móvil", "Celular", "Mobile number"],
    ["用于辅助验证与账号找回", "Para verificación y recuperación de cuenta", "Para verificação e recuperação da conta", "For verification and account recovery"],
    ["工作邮箱", "Correo de trabajo", "E-mail profissional", "Work email"],
    ["密码", "Contraseña", "Senha", "Password"],
    ["确认密码", "Confirmar contraseña", "Confirmar senha", "Confirm password"],
    ["至少 8 位", "Mínimo 8 caracteres", "Mínimo de 8 caracteres", "At least 8 characters"],
    ["请输入密码", "Escribe tu contraseña", "Digite sua senha", "Enter your password"],
    ["再次输入密码", "Repite la contraseña", "Digite a senha novamente", "Enter the password again"],
    ["创建账号并选择会员", "Crear cuenta y elegir plan", "Criar conta e escolher plano", "Create account and choose plan"],
    ["登录并进入工作台", "Entrar al sistema", "Entrar no sistema", "Sign in to workspace"],
    ["查看产品详情", "Ver producto", "Ver produto", "View product"],
    ["每个账号拥有独立账号 Key，每份客户档案按机构隔离访问。Computer Use 仅辅助写入经核对的信息，不处理验证码、电子签名或最终提交。", "Cada cuenta tiene una clave propia y los expedientes están aislados por agencia. Computer Use solo escribe información revisada; no maneja CAPTCHA, firma electrónica ni envío final.", "Cada conta tem uma chave própria e os casos são isolados por agência. O Computer Use apenas insere informações revisadas; não trata CAPTCHA, assinatura eletrônica nem envio final.", "Each account has its own key and cases are isolated by agency. Computer Use only enters reviewed information; it does not handle CAPTCHA, electronic signature or final submission."],
    ["基础信息 · 护照 · 旅行 · 家庭 · 工作教育", "Datos personales · Pasaporte · Viaje · Familia · Trabajo y estudios", "Dados pessoais · Passaporte · Viagem · Família · Trabalho e estudos", "Personal · Passport · Travel · Family · Work and education"],
    ["初稿已生成", "Borrador generado", "Rascunho gerado", "Draft generated"],
    ["拒签记录 · 赴美历史 · 背景问题 · SEVIS 信息", "Rechazos · Viajes a EE. UU. · Seguridad · SEVIS", "Recusas · Viagens aos EUA · Segurança · SEVIS", "Refusals · U.S. travel · Security · SEVIS"],
    ["敏感背景问题只做提醒，不自动代填；提交前由顾问逐项确认", "Las preguntas sensibles solo se señalan; no se contestan automáticamente. El asesor confirma cada punto antes del envío.", "Perguntas sensíveis apenas recebem alertas; não são preenchidas automaticamente. O consultor confirma cada item antes do envio.", "Sensitive background questions are flagged, not answered automatically. The consultant confirms each item before submission."],
    ["开始使用", "Comenzar", "Começar", "Get started"],
    ["登录后，直接处理客户档案。", "Inicia sesión y trabaja en tus expedientes.", "Entre e trabalhe nos casos dos seus clientes.", "Sign in and work on client cases."],
    ["整理材料、核查待确认项并生成 DS-160 初稿，全部在同一工作台完成。", "Organiza documentos, resuelve pendientes y genera el borrador DS-160 en un solo lugar.", "Organize documentos, resolva pendências e gere o rascunho DS-160 em um só lugar.", "Organise documents, resolve open items and generate the DS-160 draft in one workspace."],
    ["查看产品介绍", "Ver presentación", "Ver apresentação", "View product overview"],
    ["WestoryVisa 是签证顾问及机构客户使用的软件辅助工具，并非美国政府、美国国务院、任何使领馆或签证签发机构的官方网站或授权代表；不提供法律意见，也不保证签证申请获批。", "WestoryVisa es una herramienta para asesores y agencias de visas. No es un sitio oficial ni representante autorizado del Gobierno de Estados Unidos, el Departamento de Estado, una embajada, consulado o autoridad de visas; no ofrece asesoría legal ni garantiza la aprobación.", "WestoryVisa é uma ferramenta para consultores e agências de vistos. Não é site oficial nem representante autorizado do Governo dos Estados Unidos, Departamento de Estado, embaixada, consulado ou autoridade de vistos; não oferece assessoria jurídica nem garante aprovação.", "WestoryVisa is a software tool for visa consultants and agencies. It is not an official website or authorised representative of the U.S. Government, Department of State, any embassy, consulate or visa authority; it does not provide legal advice or guarantee approval."],
    ["材料识别、翻译、字段整理、信息核查、DS‑160 初稿和可见页面填写结果均须由申请人或其授权顾问人工核对。申请人应对最终提交信息的真实性、准确性、完整性和时效性承担责任。", "El solicitante o su asesor autorizado debe revisar los resultados de lectura, traducción, organización, validación, borrador DS-160 y llenado visible. El solicitante responde por la veracidad, exactitud, integridad y vigencia del envío final.", "O solicitante ou consultor autorizado deve revisar os resultados de leitura, tradução, organização, validação, rascunho DS-160 e preenchimento visível. O solicitante responde pela veracidade, exatidão, integridade e atualidade do envio final.", "The applicant or authorised consultant must review document reading, translation, organisation, validation, DS-160 draft and visible-page entry. The applicant remains responsible for the truth, accuracy, completeness and currency of the final submission."],
    ["验证码、账户凭证、拒签或移民历史判断、安全与背景问题、电子签名、法律声明、政府费用支付及最终提交必须由申请人或经授权的顾问人工处理。", "CAPTCHA, credenciales, decisiones sobre rechazos o historial migratorio, preguntas de seguridad, firma electrónica, declaraciones legales, pagos oficiales y envío final deben ser atendidos por el solicitante o un asesor autorizado.", "CAPTCHA, credenciais, decisões sobre recusas ou histórico migratório, perguntas de segurança, assinatura eletrônica, declarações legais, pagamentos oficiais e envio final devem ser tratados pelo solicitante ou consultor autorizado.", "CAPTCHA, credentials, decisions about refusals or immigration history, security questions, electronic signature, legal declarations, government fees and final submission must be handled by the applicant or an authorised consultant."],
    ["案件中可能包含身份、护照、联系方式、旅行、教育、工作和签证申请材料；相关信息仅用于账户管理、材料整理、字段核查、服务安全及履行合同。第三方处理及跨境传输说明见", "Los expedientes pueden incluir documentos de identidad, pasaporte, contacto, viaje, educación, trabajo y solicitud. Se usan solo para administrar cuentas, organizar y revisar datos, proteger el servicio y cumplir el contrato. Consulta el", "Os casos podem incluir documentos de identidade, passaporte, contato, viagem, educação, trabalho e solicitação. São usados apenas para administrar contas, organizar e revisar dados, proteger o serviço e cumprir o contrato. Consulte a", "Cases may include identity, passport, contact, travel, education, employment and application documents. They are used only for account management, organisation and review, service security and contract performance. See the"],
    ["我们采用传输加密、权限隔离、访问日志和最小权限等合理安全措施，但任何网络系统均无法保证绝对安全。请妥善保管登录凭证、限制团队权限，并及时删除不再需要的导出文件。", "Aplicamos cifrado en tránsito, aislamiento de permisos, registros de acceso y privilegio mínimo, pero ningún sistema es totalmente seguro. Protege tus credenciales, limita permisos del equipo y borra exportaciones innecesarias.", "Aplicamos criptografia em trânsito, isolamento de permissões, registros de acesso e privilégio mínimo, mas nenhum sistema é totalmente seguro. Proteja credenciais, limite permissões da equipe e exclua exportações desnecessárias.", "We use encryption in transit, permission isolation, access logs and least privilege, but no network system is completely secure. Protect credentials, limit team permissions and delete exports that are no longer needed."],
    ["机构工作台", "Sistema de la agencia", "Sistema da agência", "Agency workspace"],
    ["会员与账户", "Membresía y cuenta", "Assinatura e conta", "Membership and account"],
    ["中介机构填写辅助工具", "Sistema de apoyo para agencias", "Sistema de apoio para agências", "Agency filing assistant"],
    ["使用边界：工具只辅助资料整理和初稿生成，不提供法律建议，不替代顾问人工判断。", "Alcance: la herramienta organiza documentos y genera borradores. No ofrece asesoría legal ni sustituye el criterio del asesor.", "Escopo: a ferramenta organiza documentos e gera rascunhos. Não oferece assessoria jurídica nem substitui o julgamento do consultor.", "Scope: the tool organises documents and generates drafts. It does not provide legal advice or replace consultant judgement."],
    ["客户 DS-160 档案", "Expedientes DS-160", "Casos DS-160", "Client DS-160 cases"],
    ["创建客户档案", "Crear expediente", "Criar caso", "Create client case"],
    ["只需建立客户归属和签证类别。护照号等资料可在上传后自动识别，不必在这里重复录入。", "Define la agencia responsable y el tipo de visa. El pasaporte y otros datos se leen al cargar documentos, sin capturarlos dos veces.", "Defina a agência responsável e o tipo de visto. Passaporte e outros dados são lidos após o envio, sem digitação duplicada.", "Set the responsible agency and visa type. Passport and other details can be read after upload, without duplicate entry."],
    ["F1 学生签证", "Visa de estudiante F1", "Visto de estudante F1", "F1 student visa"],
    ["适用于 I-20、SEVIS、学校信息和资金材料核对场景。", "Para revisar I-20, SEVIS, datos de la escuela y comprobantes financieros.", "Para revisar I-20, SEVIS, dados da instituição e comprovantes financeiros.", "For reviewing I-20, SEVIS, school details and financial evidence."],
    ["系统将保存为本地客户档案。关键字段、敏感背景问题和最终 DS-160 内容仍需文案老师 / 签证顾问确认。", "El expediente se guardará localmente. Un asesor debe confirmar los campos clave, las preguntas sensibles y el contenido final del DS-160.", "O caso será salvo localmente. Um consultor deve confirmar campos-chave, perguntas sensíveis e o conteúdo final do DS-160.", "The case will be saved locally. A consultant must confirm key fields, sensitive questions and the final DS-160 content."],
    ["收集客户资料", "Recopilar documentos del cliente", "Coletar documentos do cliente", "Collect client documents"],
    ["文件将安全保存到当前机构的客户档案，并进行版面解析、中英文识别和 DS-160 字段映射。支持 PDF、PNG、JPG 和 TIFF，单个文件不超过 25 MB。", "Los archivos se guardan en el expediente de la agencia y se procesan para leer el diseño, reconocer texto y mapear campos DS-160. Admite PDF, PNG, JPG y TIFF de hasta 25 MB por archivo.", "Os arquivos são salvos no caso da agência e processados para leitura de layout, reconhecimento de texto e mapeamento dos campos DS-160. Aceita PDF, PNG, JPG e TIFF de até 25 MB por arquivo.", "Files are stored in the agency case and processed for layout, text recognition and DS-160 field mapping. PDF, PNG, JPG and TIFF are supported up to 25 MB per file."],
    ["墨西哥独立版本", "Versión independiente de México", "Versão independente do México", "Mexico isolated version"],
    ["巴西独立版本", "Versión independiente de Brasil", "Versão independente do Brasil", "Brazil isolated version"],
    ["印度独立版本", "Versión independiente de India", "Versão independente da Índia", "India isolated version"],
    ["中国现有版本", "Versión actual de China", "Versão atual da China", "Existing China version"],
    ["返回", "Volver", "Voltar", "Back"],
    ["文档扫描服务", "Servicio de lectura documental", "Serviço de leitura documental", "Document scanning service"],
    ["尚未安装本地文档扫描环境", "El entorno local de lectura no está instalado", "O ambiente local de leitura não está instalado", "Local document scanning is not installed"],
    ["尚未安装", "No instalado", "Não instalado", "Not installed"],
    ["启动扫描服务", "Iniciar lector", "Iniciar leitor", "Start scanning service"],
    ["顾问已知信息", "Datos ya conocidos por el asesor", "Dados já conhecidos pelo consultor", "Information already known to the consultant"],
    ["粘贴客户已经提供的文字", "Pega el texto ya enviado por el cliente", "Cole o texto já enviado pelo cliente", "Paste text already provided by the client"],
    ["可直接粘贴微信、邮件或笔记中的连续文字。系统先扫描 DS-160 字段，再用本地语义模型补漏，最后统一转换为可核查的英文值。", "Puedes pegar texto de WhatsApp, correo o notas. El sistema detecta campos DS-160, completa vacíos con el modelo semántico y prepara valores en inglés para revisión.", "Você pode colar texto de WhatsApp, e-mail ou anotações. O sistema detecta campos DS-160, completa lacunas com o modelo semântico e prepara valores em inglês para revisão.", "Paste text from messages, email or notes. The system detects DS-160 fields, fills gaps with the semantic model and prepares reviewable English values."],
    ["DeepSeek 中译英尚未连接。规则仍会识别证件号、日期和固定选项，但中文地址、学校、公司及职责暂不能保证为正常英文语序。请联系管理员检查 DeepSeek 服务配置。", "La traducción de DeepSeek no está conectada. Las reglas aún reconocen documentos, fechas y opciones fijas, pero no se garantiza la redacción inglesa de direcciones, escuelas, empresas o funciones. Pide al administrador revisar la configuración.", "A tradução do DeepSeek não está conectada. As regras ainda reconhecem documentos, datas e opções fixas, mas não há garantia da redação em inglês para endereços, escolas, empresas ou funções. Peça ao administrador para revisar a configuração.", "DeepSeek translation is not connected. Rules still recognise document numbers, dates and fixed choices, but English phrasing for addresses, schools, companies and duties is not guaranteed. Ask the administrator to check the service configuration."],
    ["系统会先区分题目与回答，再整理直接字段、条件问答和重复记录；原文与识别证据始终保留。", "El sistema separa preguntas y respuestas, organiza campos directos, respuestas condicionales y registros repetidos, y conserva siempre el texto y la evidencia originales.", "O sistema separa perguntas e respostas, organiza campos diretos, respostas condicionais e registros repetidos e preserva sempre o texto e a evidência originais.", "The system separates questions from answers, organises direct fields, conditional answers and repeated records, and always keeps original text and evidence."],
    ["识别、翻译并整理", "Leer, traducir y organizar", "Ler, traduzir e organizar", "Read, translate and organise"],
    ["客户材料库", "Biblioteca del cliente", "Biblioteca do cliente", "Client document library"],
    ["已保存原始文件", "Archivos originales guardados", "Arquivos originais salvos", "Original files saved"],
    ["原件保存在当前机构的客户档案中，仅登录本机构账号后可以查看。", "Los originales quedan en el expediente y solo pueden verse con una cuenta de esta agencia.", "Os originais ficam no caso e só podem ser vistos por uma conta desta agência.", "Originals stay in the case and can be viewed only by an account from this agency."],
    ["0 份", "0 archivos", "0 arquivos", "0 files"],
    ["尚未保存客户材料", "Aún no hay documentos", "Ainda não há documentos", "No client documents saved"],
    ["上传后，原件、识别文字与字段来源会统一保留在这里。", "Al cargar, aquí se conservan el original, el texto leído y la fuente de cada campo.", "Após o envio, o original, o texto lido e a fonte de cada campo ficam guardados aqui.", "After upload, the original, recognised text and field source are kept here."],
    ["开始扫描并生成初稿", "Iniciar lectura y generar borrador", "Iniciar leitura e gerar rascunho", "Start scan and generate draft"],
    ["重新检查扫描服务", "Volver a comprobar el lector", "Verificar o leitor novamente", "Check scanning service again"],
    ["暂不扫描，进入字段核查", "Omitir lectura e revisar campos", "Pular leitura e revisar campos", "Skip scan and review fields"],
    ["保存草稿", "Guardar borrador", "Salvar rascunho", "Save draft"],
    ["关键字段复核", "Revisión de campos clave", "Revisão de campos-chave", "Key field review"],
    ["这里只核查已经从材料中提取出的关键、低置信度或冲突内容。材料中没有的信息不会要求中介代填，下一步会自动整理成客户补充链接。", "Revisa solo campos clave, de baja confianza o con conflicto que fueron extraídos. Lo que no está en los documentos pasará a un enlace para que el cliente lo complete; la agencia no debe inventarlo.", "Revise apenas campos-chave, de baixa confiança ou com conflito que foram extraídos. O que não está nos documentos vai para um link de complemento do cliente; a agência não deve inventar.", "Review only extracted fields that are key, low-confidence or conflicting. Missing information moves to a client follow-up link; the agency is not asked to invent it."],
    ["已确认或系统校验", "Confirmados o validados", "Confirmados ou validados", "Confirmed or validated"],
    ["关键待复核", "Campos clave por revisar", "Campos-chave a revisar", "Key fields to review"],
    ["无需逐项点击", "Sin clic individual", "Sem clique individual", "No item-by-item click"],
    ["重点复核", "Revisión prioritaria", "Revisão prioritária", "Priority review"],
    ["只确认会影响初稿的内容", "Confirma solo lo que afecta el borrador", "Confirme apenas o que afeta o rascunho", "Confirm only what affects the draft"],
    ["0 项", "0 elementos", "0 itens", "0 items"],
    ["DS-160 字段", "Campo DS-160", "Campo DS-160", "DS-160 field"],
    ["填写建议", "Valor sugerido", "Valor sugerido", "Suggested value"],
    ["来源材料", "Documento fuente", "Documento de origem", "Source document"],
    ["置信度", "Confianza", "Confiança", "Confidence"],
    ["风险", "Riesgo", "Risco", "Risk"],
    ["状态", "Estado", "Status", "Status"],
    ["操作", "Acción", "Ação", "Action"],
    ["没有需要逐项处理的已提取字段", "No hay campos extraídos que requieran revisión individual", "Não há campos extraídos que exijam revisão individual", "No extracted fields need individual review"],
    ["材料中缺少的信息会在下一步自动整理成客户补充表。", "La información faltante se organizará en el formulario para el cliente.", "As informações ausentes serão organizadas no formulário do cliente.", "Missing information will be organised into the client follow-up form."],
    ["确认关键字段并继续", "Confirmar campos clave y continuar", "Confirmar campos-chave e continuar", "Confirm key fields and continue"],
    ["返回客户材料", "Volver a documentos", "Voltar aos documentos", "Back to client documents"],
    ["落地页数据", "Datos del sitio", "Dados do site", "Website analytics"],
    ["今天", "Hoy", "Hoje", "Today"],
    ["客户档案", "Expedientes", "Casos de clientes", "Client cases"],
    ["待人工核查", "Pendientes de revisión", "Aguardando revisão", "Awaiting review"],
    ["已生成初稿", "Borradores generados", "Rascunhos gerados", "Drafts generated"],
    ["查看落地页", "Ver sitio", "Ver site", "View website"],
    ["返回机构接入", "Volver al acceso", "Voltar ao acesso", "Back to agency access"],
    ["退出账号", "Cerrar sesión", "Sair", "Sign out"],
    ["继续处理", "Continuar", "Continuar", "Continue"],
    ["客户文档库", "Documentos", "Documentos", "Documents"],
    ["填写进度", "Avance", "Andamento", "Progress"],
    ["客户姓名", "Nombre del cliente", "Nome do cliente", "Client name"],
    ["护照号", "Número de pasaporte", "Número do passaporte", "Passport number"],
    ["可稍后自动识别", "Se puede leer después", "Pode ser lido depois", "Can be read later"],
    ["负责人", "Responsable", "Responsável", "Owner"],
    ["申请版本", "Versión del país", "Versão do país", "Country version"],
    ["国家版本由机构账号固定，不能在客户档案中切换。", "El país está fijado por la cuenta de la agencia y no se cambia dentro del expediente.", "O país é definido pela conta da agência e não pode ser alterado dentro do caso.", "The country is fixed by the agency account and cannot be changed inside a case."],
    ["签证类型", "Tipo de visa", "Tipo de visto", "Visa type"],
    ["内部备注", "Notas internas", "Observações internas", "Internal notes"],
    ["创建客户档案", "Crear expediente", "Criar caso", "Create client case"],
    ["返回工作台", "Volver al panel", "Voltar ao painel", "Back to workspace"],
    ["正在创建…", "Creando…", "Criando…", "Creating…"],
    ["客户档案固定归属当前登录机构。", "El expediente pertenece siempre a la agencia que inició sesión.", "O caso pertence sempre à agência conectada.", "The case always belongs to the signed-in agency."],
    ["资料收集中", "Recopilando documentos", "Coletando documentos", "Collecting documents"],
    ["工作台", "Panel", "Painel", "Workspace"],
    ["上一步", "Paso anterior", "Etapa anterior", "Previous step"],
    ["下一步", "Siguiente", "Próxima", "Next"],
    ["保存", "Guardar", "Salvar", "Save"],
    ["取消", "Cancelar", "Cancelar", "Cancel"],
    ["关闭", "Cerrar", "Fechar", "Close"],
    ["待处理", "Pendiente", "Pendente", "Pending"],
    ["未上传", "Sin cargar", "Não enviado", "Not uploaded"],
    ["上传中", "Cargando", "Enviando", "Uploading"],
    ["待扫描", "Por leer", "Aguardando leitura", "Waiting to scan"],
    ["等待扫描", "En cola", "Na fila", "Queued"],
    ["整理中", "Procesando", "Processando", "Processing"],
    ["已完成", "Completado", "Concluído", "Completed"],
    ["已确认", "Confirmado", "Confirmado", "Confirmed"],
    ["已编辑", "Editado", "Editado", "Edited"],
    ["已解决", "Resuelto", "Resolvido", "Resolved"],
    ["高风险", "Riesgo alto", "Risco alto", "High risk"],
    ["中风险", "Riesgo medio", "Risco médio", "Medium risk"],
    ["低风险", "Riesgo bajo", "Risco baixo", "Low risk"],
    ["当前测试阶段暂不进行验证码校验；手机号会作为后续账号验证与找回信息保存，请妥善保管账号密码。", "En esta etapa de prueba no se valida un código. El teléfono se guardará para verificación y recuperación; protege la contraseña de la cuenta.", "Nesta fase de teste não há validação de código. O telefone será salvo para verificação e recuperação; proteja a senha da conta.", "No verification code is checked in this test phase. The phone number is saved for account verification and recovery; keep the account password secure."],
    ["账号入口", "Acceso a la cuenta", "Acesso à conta", "Account access"],
    ["例如：上海 XX 留学服务中心", "Ej.: Agencia de Visas Ciudad de México", "Ex.: Agência de Vistos São Paulo", "e.g. Delhi Visa Services"],
    ["文案老师 / 签证顾问姓名", "Nombre del asesor de visas", "Nome do consultor de vistos", "Visa consultant name"],
    ["例如：138 0000 0000", "Ej.: 55 1234 5678", "Ex.: (11) 91234-5678", "e.g. 98765 43210"],
    ["WestoryVisa 工作台预览", "Vista del sistema WestoryVisa", "Visão do sistema WestoryVisa", "WestoryVisa workspace preview"],
    ["工作台使用说明", "Avisos de uso del sistema", "Avisos de uso do sistema", "Workspace use notices"],
    ["返回操作台首页", "Volver al panel principal", "Voltar ao painel principal", "Back to workspace home"],
    ["工作台导航", "Navegación del sistema", "Navegação do sistema", "Workspace navigation"],
    ["返回上一页", "Volver", "Voltar", "Go back"],
    ["返回机构工作台", "Volver al sistema de la agencia", "Voltar ao sistema da agência", "Back to agency workspace"],
    ["例如：张伟 / ZHANG WEI", "Ej.: MARÍA LÓPEZ", "Ex.: MARIA SILVA", "e.g. PRIYA SHARMA"],
    ["无需重复录入，可留空", "Opcional; se puede leer del documento", "Opcional; pode ser lido do documento", "Optional; can be read from the document"],
    ["可记录客户材料缺口、顾问提醒、已沟通事项、二审关注点等", "Anota documentos faltantes, recordatorios, acuerdos y puntos para segunda revisión.", "Anote documentos ausentes, lembretes, acordos e pontos para a segunda revisão.", "Record missing documents, reminders, agreed points and second-review notes."],
    ["流程导航", "Navegación del flujo", "Navegação do fluxo", "Workflow navigation"],
    ["客户补充与重点复核", "Datos del cliente y revisión prioritaria", "Dados do cliente e revisão prioritária", "Client details and priority review"],
    ["材料中没有的信息由系统整理成客户补充链接。客户提交后自动回流到本档案，顾问只核查关键字段、冲突和敏感问题。", "El sistema convierte los datos faltantes en un formulario para el cliente. Las respuestas vuelven a este expediente y el asesor revisa solo campos clave, conflictos y temas sensibles.", "O sistema transforma os dados ausentes em um formulário para o cliente. As respostas voltam para este caso e o consultor revisa apenas campos essenciais, conflitos e questões sensíveis.", "The system turns missing details into a client form. Responses return to this case, and the consultant reviews only key fields, conflicts and sensitive matters."],
    ["材料自动判断", "Determinados por documentos", "Definidos pelos documentos", "Determined from documents"],
    ["重要项待复核", "Prioridades por revisar", "Prioridades a revisar", "Priority items to review"],
    ["背景题待最终确认", "Antecedentes por confirmar", "Antecedentes a confirmar", "Background questions to confirm"],
    ["客户补充链接", "Enlace para el cliente", "Link para o cliente", "Client form link"],
    ["只向客户询问材料中没有的信息", "Pregunta solo lo que falta en los documentos", "Pergunte apenas o que falta nos documentos", "Ask only for details missing from the documents"],
    ["护照、行程、I-20 / DS-2019 等材料已经提供的内容不会重复提问。剩余问题会转换成通俗中文表单，客户提交后直接写回当前档案。", "No se repite lo que ya aparece en el pasaporte, itinerario, I-20 o DS-2019. Las preguntas restantes se muestran en español claro y las respuestas se guardan directamente en el expediente.", "O que já consta no passaporte, itinerário, I-20 ou DS-2019 não é perguntado novamente. As perguntas restantes aparecem em português claro e as respostas são salvas diretamente no caso.", "Details already found in the passport, itinerary, I-20 or DS-2019 are not asked again. The remaining questions use clear English and the answers are saved directly to the case."],
    ["尚未生成", "Aún no creado", "Ainda não criado", "Not created"],
    ["生成客户补充链接", "Crear enlace para el cliente", "Criar link para o cliente", "Create client form link"],
    ["本地版用于流程测试。正式发送前需要将网站与数据库部署到 HTTPS 公网地址。", "Esta versión local sirve para probar el flujo. Antes de enviarlo a un cliente, publica el sitio y la base de datos en una dirección HTTPS.", "Esta versão local serve para testar o fluxo. Antes de enviar ao cliente, publique o site e o banco de dados em um endereço HTTPS.", "This local version is for workflow testing. Before sending it to a client, deploy the site and database to an HTTPS address."],
    ["扫描判断结果", "Resultado de la revisión documental", "Resultado da análise dos documentos", "Document review result"],
    ["优先处理重要内容", "Revisa primero lo importante", "Revise primeiro o que importa", "Review important items first"],
    ["系统不会把材料未提及的信息擅自填为 No；客户补充内容会在这里集中呈现。", "El sistema nunca marca No cuando los documentos no contienen la respuesta. Los datos enviados por el cliente se concentran aquí.", "O sistema nunca marca Não quando os documentos não trazem a resposta. Os dados enviados pelo cliente ficam reunidos aqui.", "The system never selects No when the documents do not contain an answer. Client responses appear here."],
    ["查看完整问题清单", "Ver todas las preguntas", "Ver todas as perguntas", "View all questions"],
    ["客户确认", "Confirmado por el cliente", "Confirmado pelo cliente", "Client confirmation"],
    ["信息待补充", "Faltan datos", "Dados pendentes", "Missing details"],
    ["核对", "Revisar", "Revisar", "Review"],
    ["背景与历史问题", "Antecedentes e historial", "Antecedentes e histórico", "Background and history"],
    ["进入完整清单", "Abrir lista completa", "Abrir lista completa", "Open full list"],
    ["进入风险复核", "Ir a revisión de riesgos", "Ir para revisão de riscos", "Go to risk review"],
    ["返回关键字段", "Volver a campos clave", "Voltar aos campos essenciais", "Back to key fields"],
    ["当前待补充字段", "Campos pendientes", "Campos pendentes", "Missing fields"],
    ["DS-160 条件问答", "Preguntas condicionales del DS-160", "Perguntas condicionais do DS-160", "DS-160 conditional questions"],
    ["按 DS-160 分支逐项确认。材料可辅助预填客观字段；历史、健康、犯罪、移民与安全问题不会由系统推断或默认选择 No。", "Confirma cada rama del DS-160. Los documentos pueden completar datos objetivos, pero el sistema no infiere antecedentes, salud, delitos, inmigración ni seguridad, ni selecciona No por defecto.", "Confirme cada ramificação do DS-160. Os documentos podem preencher dados objetivos, mas o sistema não deduz antecedentes, saúde, crimes, imigração ou segurança nem seleciona Não por padrão.", "Confirm each DS-160 branch. Documents may prefill objective facts, but the system never infers history, health, criminal, immigration or security answers or defaults them to No."],
    ["完整问题清单", "Lista completa de preguntas", "Lista completa de perguntas", "Full question list"],
    ["分支会随签证类别和已选答案动态变化；最终以客户当次 CEAC 页面为准。", "Las ramas cambian según la visa y las respuestas. La página de CEAC del cliente es la referencia final.", "As ramificações mudam conforme o visto e as respostas. A página do CEAC do cliente é a referência final.", "Branches change with visa type and answers. The client's current CEAC page is the final reference."],
    ["返回重点视图", "Volver a prioridades", "Voltar às prioridades", "Back to priority view"],
    ["已回答或已核查", "Respondidas o revisadas", "Respondidas ou revisadas", "Answered or reviewed"],
    ["敏感题待顾问核查", "Preguntas sensibles por revisar", "Perguntas sensíveis a revisar", "Sensitive questions to review"],
    ["资料字段待客户补充", "Datos que debe completar el cliente", "Dados a completar pelo cliente", "Details needed from client"],
    ["返回客户补充链接", "Volver al enlace del cliente", "Voltar ao link do cliente", "Back to client form link"],
    ["DS-160 问题模块", "Secciones de preguntas del DS-160", "Seções de perguntas do DS-160", "DS-160 question sections"],
    ["条件问答", "Preguntas condicionales", "Perguntas condicionais", "Conditional questions"],
    ["保存当前模块", "Guardar esta sección", "Salvar esta seção", "Save this section"],
    ["下一模块", "Siguiente sección", "Próxima seção", "Next section"],
    ["复核全部问题", "Revisar todas las preguntas", "Revisar todas as perguntas", "Review all questions"],
    ["必须人工确认", "Confirmación manual obligatoria", "Confirmação manual obrigatória", "Manual confirmation required"],
    ["选择当前答案", "Selecciona la respuesta", "Selecione a resposta", "Select the answer"],
    ["请选择", "Selecciona", "Selecione", "Select"],
    ["材料自动判断 ·", "Determinado por documentos ·", "Definido pelos documentos ·", "Determined from documents ·"],
    ["上传材料", "Documentos cargados", "Documentos enviados", "Uploaded documents"],
    ["客户通过补充链接提交", "Enviado por el cliente mediante el enlace", "Enviado pelo cliente pelo link", "Submitted by client through the link"],
    ["系统只提取材料中的明确答案，不会因材料未提及而默认选择 No。", "El sistema solo extrae respuestas explícitas y nunca selecciona No porque un documento no mencione el tema.", "O sistema extrai apenas respostas explícitas e nunca seleciona Não porque o documento não menciona o tema.", "The system extracts only explicit answers and never selects No because a document does not mention the topic."],
    ["顾问已核查", "Revisado por el asesor", "Revisado pelo consultor", "Reviewed by consultant"],
    ["标记顾问已核查", "Marcar como revisado", "Marcar como revisado", "Mark as reviewed"],
    ["查看建议核对资料", "Ver documentos sugeridos", "Ver documentos sugeridos", "View suggested documents"],
    ["记录", "Registro", "Registro", "Record"],
    ["+ 添加一项", "+ Agregar", "+ Adicionar", "+ Add record"],
    ["删除本条记录", "Eliminar este registro", "Excluir este registro", "Remove this record"],
    ["尚未添加记录。选择适用答案后，请按客户真实情况逐项添加。", "Aún no hay registros. Elige la respuesta correspondiente y agrega los datos reales del cliente.", "Ainda não há registros. Selecione a resposta aplicável e adicione os dados reais do cliente.", "No records yet. Select the applicable answer and add the client's actual details."],
    ["待客户确认", "Pendiente del cliente", "Aguardando o cliente", "Waiting for client"],
    ["项当前待补充", "datos pendientes", "dados pendentes", "details pending"],
    ["· 客户确认", "· Confirmado por el cliente", "· Confirmado pelo cliente", "· Client confirmation"],
    ["已核查", "Revisado", "Revisado", "Reviewed"],
    ["已回答", "Respondido", "Respondido", "Answered"],
    ["客户已补充", "Completado por el cliente", "Preenchido pelo cliente", "Completed by client"],
    ["需顾问判断", "Requiere criterio del asesor", "Requer avaliação do consultor", "Consultant judgement required"],
    ["已填写", "Completado", "Preenchido", "Completed"],
    ["请输入客户已确认的信息", "Ingresa la información confirmada por el cliente", "Informe os dados confirmados pelo cliente", "Enter information confirmed by the client"],
    ["请输入", "Ingresa", "Informe", "Enter"],
    ["重点风险复核", "Revisión de riesgos prioritarios", "Revisão dos principais riscos", "Priority risk review"],
    ["系统已隐藏低影响和已处理项目，只保留材料冲突、关键缺失、明确的 Yes，以及需要顾问最终确认的背景问题。", "Se ocultan los puntos resueltos o de bajo impacto. Aquí solo aparecen conflictos, datos clave faltantes, respuestas Yes explícitas y antecedentes que requieren confirmación final del asesor.", "Itens resolvidos ou de baixo impacto ficam ocultos. Aqui aparecem apenas conflitos, dados essenciais ausentes, respostas Yes explícitas e antecedentes que exigem confirmação final do consultor.", "Resolved and low-impact items are hidden. This view keeps document conflicts, key missing details, explicit Yes answers and background questions requiring final consultant confirmation."],
    ["项需要关注", "puntos requieren atención", "itens exigem atenção", "items need attention"],
    ["项已处理或无需阻塞当前流程", "puntos resueltos o que no bloquean este flujo", "itens resolvidos ou que não bloqueiam este fluxo", "items resolved or not blocking this workflow"],
    ["发现识别值有误时，可返回字段核查直接修改，修改会自动保留。", "Si un dato leído es incorrecto, vuelve a la revisión de campos y edítalo. El cambio se guardará automáticamente.", "Se um dado lido estiver incorreto, volte à revisão de campos e edite-o. A alteração será salva automaticamente.", "If an extracted value is wrong, return to field review and edit it. The change is saved automatically."],
    ["修改识别字段", "Editar campos extraídos", "Editar campos extraídos", "Edit extracted fields"],
    ["返回条件问答", "Volver a preguntas condicionales", "Voltar às perguntas condicionais", "Back to conditional questions"],
    ["安全与背景问题", "Seguridad y antecedentes", "Segurança e antecedentes", "Security and background"],
    ["查看 DS-160 初稿", "Ver borrador del DS-160", "Ver rascunho do DS-160", "View DS-160 draft"],
    ["该模块暂无可用资料", "Aún no hay datos disponibles para esta sección", "Ainda não há dados disponíveis nesta seção", "No data is available for this section yet"],
    ["待录入 / 待确认", "Pendiente de captura o confirmación", "Aguardando preenchimento ou confirmação", "Awaiting entry or confirmation"],
    ["是否有人同行", "¿Viaja alguien con el solicitante?", "Alguém viajará com o solicitante?", "Is anyone travelling with the applicant?"],
    ["同行人姓名", "Nombre del acompañante", "Nome do acompanhante", "Travel companion's name"],
    ["与申请人的关系", "Relación con el solicitante", "Relação com o solicitante", "Relationship to the applicant"],
    ["是否作为团队或组织出行", "¿Viaja como parte de un grupo u organización?", "Viaja como parte de um grupo ou organização?", "Travelling as part of a group or organisation?"],
    ["是否曾去过美国", "¿Ha viajado antes a Estados Unidos?", "Já viajou para os Estados Unidos?", "Previously travelled to the United States?"],
    ["过往赴美日期", "Fechas de viajes anteriores a Estados Unidos", "Datas de viagens anteriores aos Estados Unidos", "Dates of previous U.S. travel"],
    ["是否持有或曾持有美国签证", "¿Tiene o tuvo una visa estadounidense?", "Possui ou já possuiu visto dos Estados Unidos?", "Has or previously had a U.S. visa?"],
    ["签证号码", "Número de visa", "Número do visto", "Visa number"],
    ["拒签 / 拒绝入境 / 移民申请记录", "Rechazos de visa, negativas de entrada o solicitudes migratorias", "Recusas de visto, negativas de entrada ou pedidos de imigração", "Visa refusals, denied entry or immigration petitions"],
    ["父母姓名和出生日期", "Nombres y fechas de nacimiento de los padres", "Nomes e datas de nascimento dos pais", "Parents' names and dates of birth"],
    ["父母是否在美国", "¿Los padres están en Estados Unidos?", "Os pais estão nos Estados Unidos?", "Are the parents in the United States?"],
    ["配偶信息", "Datos del cónyuge", "Dados do cônjuge", "Spouse details"],
    ["子女信息", "Datos de los hijos", "Dados dos filhos", "Children's details"],
    ["在美直系亲属或其他亲属情况", "Familiares inmediatos u otros familiares en Estados Unidos", "Familiares imediatos ou outros parentes nos Estados Unidos", "Immediate or other relatives in the United States"],
    ["当前职业", "Ocupación actual", "Ocupação atual", "Current occupation"],
    ["当前雇主 / 学校", "Empleador o escuela actual", "Empregador ou instituição atual", "Current employer or school"],
    ["职位 / 专业", "Puesto o área de estudios", "Cargo ou área de estudos", "Job title or field of study"],
    ["地址和联系方式", "Domicilio y datos de contacto", "Endereço e contato", "Address and contact details"],
    ["过往工作和教育经历", "Empleos y estudios anteriores", "Histórico profissional e acadêmico", "Previous work and education"],
    ["特殊培训 / 语言能力 / 旅行国家记录", "Capacitación especializada, idiomas y países visitados", "Treinamento especializado, idiomas e países visitados", "Specialised training, languages and countries visited"],
    ["健康相关问题", "Preguntas de salud", "Perguntas de saúde", "Health-related questions"],
    ["犯罪记录", "Antecedentes penales", "Antecedentes criminais", "Criminal history"],
    ["移民违规", "Infracciones migratorias", "Violações de imigração", "Immigration violations"],
    ["安全相关问题", "Preguntas de seguridad", "Perguntas de segurança", "Security questions"],
    ["特殊组织 / 军事 / 执法 / 专业技能", "Organizaciones especiales, servicio militar, seguridad pública o habilidades especializadas", "Organizações especiais, serviço militar, segurança pública ou habilidades especializadas", "Special organisations, military, law enforcement or specialised skills"],
    ["过往拒签、拒绝入境或撤回入境申请", "Rechazos de visa, negativas de entrada o solicitudes de admisión retiradas", "Recusas de visto, negativas de entrada ou pedidos de admissão retirados", "Previous visa refusals, denied entry or withdrawn applications for admission"],
    ["需顾问逐项确认", "Confirmación individual del asesor", "Confirmação individual do consultor", "Consultant must confirm each item"],
    ["条件分支", "Ramas condicionales", "Ramificações condicionais", "Conditional branches"],
    ["DS-160 条件问答摘要", "Resumen de preguntas condicionales del DS-160", "Resumo das perguntas condicionais do DS-160", "DS-160 conditional question summary"],
    ["当前是预览模式，不能控制 Chrome", "El sistema está en modo de vista previa y no puede controlar Chrome", "O sistema está no modo de visualização e não pode controlar o Chrome", "The system is in preview mode and cannot control Chrome"],
    ["可见浏览器执行层尚未就绪。", "La capa de ejecución visible del navegador aún no está lista.", "A camada de execução visível do navegador ainda não está pronta.", "The visible browser execution layer is not ready."],
    ["进入 Computer Use 执行台", "Abrir consola de Computer Use", "Abrir painel do Computer Use", "Open Computer Use console"],
    ["Computer Use 逐页填写", "Llenado página por página con Computer Use", "Preenchimento página a página com Computer Use", "Page-by-page entry with Computer Use"],
    ["WestoryVisa 准备当前客户字段计划。你人工完成验证码并进入正式表格后，再把可见页面交给 Computer Use 稳健填写。", "WestoryVisa prepara el plan de campos del cliente. Completa manualmente el CAPTCHA y entra al formulario; después entrega la página visible a Computer Use.", "A WestoryVisa prepara o plano de campos do cliente. Conclua manualmente o CAPTCHA e entre no formulário; depois entregue a página visível ao Computer Use.", "WestoryVisa prepares the client's field plan. Complete the CAPTCHA manually and enter the form, then hand the visible page to Computer Use."],
    ["未准备", "No preparada", "Não preparada", "Not prepared"],
    ["返回客户问题补充", "Volver a datos del cliente", "Voltar aos dados do cliente", "Back to client details"],
    ["打开官方网站起始页", "Abrir la página inicial oficial", "Abrir a página inicial oficial", "Open the official start page"],
    ["当前档案还没有可交接的已收集信息。", "Este expediente aún no tiene datos listos para transferir.", "Este caso ainda não tem dados prontos para transferência.", "This case does not yet have collected data ready for handoff."],
    ["一次性令牌只保存在当前页面内存中，不写入客户数据库；任务关闭后服务器会擦除字段值。", "El token de un solo uso permanece únicamente en la memoria de esta página y no se guarda en la base de datos del cliente. Al cerrar la tarea, el servidor elimina los valores de los campos.", "O token de uso único fica apenas na memória desta página e não é salvo no banco de dados do cliente. Ao encerrar a tarefa, o servidor apaga os valores dos campos.", "The one-time token stays only in this page's memory and is not written to the client database. The server erases field values when the task closes."],
    ["开启后，当前页全部复读无误且无报错时才点击 Next", "Al activarlo, Next solo se pulsa después de verificar todos los valores de la página y confirmar que no hay errores", "Quando ativado, Next só é clicado após conferir todos os valores da página e confirmar que não há erros", "When enabled, Next is clicked only after every value on the page is read back correctly and no errors are present"],
    ["不会跨越的边界", "Límites que no se cruzan", "Limites que não serão ultrapassados", "Boundaries that are never crossed"],
    ["Computer Use 不处理验证码、登录凭据、拒签或移民历史判断、安全与背景问题、电子签名、法律声明、付款和最终提交；不使用脚本注入，也不绕过网站限制。顾问可以随时停止并人工接管。", "Computer Use no gestiona CAPTCHA, credenciales, decisiones sobre rechazos o historial migratorio, preguntas de seguridad y antecedentes, firma electrónica, declaraciones legales, pagos ni el envío final. No inyecta scripts ni evade restricciones del sitio. El asesor puede detenerlo y tomar el control en cualquier momento.", "O Computer Use não processa CAPTCHA, credenciais, decisões sobre recusas ou histórico migratório, perguntas de segurança e antecedentes, assinatura eletrônica, declarações legais, pagamentos ou envio final. Não injeta scripts nem contorna restrições do site. O consultor pode interromper e assumir o controle a qualquer momento.", "Computer Use does not handle CAPTCHA, credentials, decisions about refusals or immigration history, security and background questions, electronic signatures, legal declarations, payments or final submission. It does not inject scripts or bypass site restrictions. The consultant can stop and take over at any time."],
    ["返回 DS-160 初稿", "Volver al borrador del DS-160", "Voltar ao rascunho do DS-160", "Back to DS-160 draft"],
    ["Computer Use 执行步骤", "Pasos de Computer Use", "Etapas do Computer Use", "Computer Use steps"],
    ["Current Scope", "Alcance actual", "Escopo atual", "Current scope"],
    ["Field Plan", "Plan de campos", "Plano de campos", "Field plan"],
    ["Local Computer", "Equipo local", "Computador local", "Local computer"],
    ["Current Scope", "Alcance actual", "Escopo atual", "Current scope"],
    ["当前客户的逐页字段计划", "Plan de campos del cliente por página", "Plano de campos do cliente por página", "Client field plan by page"],
    ["当前执行状态", "Estado de ejecución", "Status da execução", "Execution status"],
    ["字段进度", "Avance de campos", "Progresso dos campos", "Field progress"],
    ["当前 Chrome", "Chrome actual", "Chrome atual", "Current Chrome"],
    ["节奏", "Ritmo", "Ritmo", "Pace"],
    ["核验后", "Tras verificar", "Após verificar", "After verification"],
    ["目标网站", "Sitio objetivo", "Site de destino", "Target site"],
    ["已记录页面路径", "Rutas registradas", "Rotas registradas", "Recorded routes"],
    ["尚未捕获表格路径", "Aún no se detecta la ruta del formulario", "A rota do formulário ainda não foi detectada", "Form route not captured yet"],
    ["尚未准备本机任务", "Tarea local aún no preparada", "Tarefa local ainda não preparada", "Local task not prepared"],
    ["普通页面连续填写", "Llenado continuo en páginas normales", "Preenchimento contínuo em páginas comuns", "Continuous entry on standard pages"],
    ["准备任务并打开 CEAC", "Preparar tarea y abrir CEAC", "Preparar tarefa e abrir o CEAC", "Prepare task and open CEAC"],
    ["WestoryVisa｜购买会员", "WestoryVisa | Membresía", "WestoryVisa | Assinatura", "WestoryVisa | Membership"],
    ["返回 WestoryVisa 机构接入首页", "Volver al inicio de WestoryVisa", "Voltar ao início da WestoryVisa", "Back to WestoryVisa home"],
    ["开始客户流程之前", "Antes de iniciar un expediente", "Antes de iniciar um caso", "Before starting a client case"],
    ["工作台导航", "Navegación del sistema", "Navegação do sistema", "Workspace navigation"],
    ["会员中心", "Membresía", "Assinatura", "Membership"],
    ["个人中心", "Perfil", "Perfil", "Profile"],
    ["帮助中心", "Ayuda", "Ajuda", "Help"],
    ["登录账号", "Iniciar sesión", "Entrar", "Sign in"],
    ["会员购买", "Compra de membresía", "Compra da assinatura", "Membership purchase"],
    ["购买会员", "Comprar membresía", "Comprar assinatura", "Buy membership"],
    ["直接在本页选择月付或年付并完成购买，支付确认后自动开通当前机构账号。", "Elige un plan mensual o anual y completa la compra en esta página. La cuenta de la agencia se activa cuando se confirma el pago.", "Escolha um plano mensal ou anual e conclua a compra nesta página. A conta da agência é ativada após a confirmação do pagamento.", "Choose a monthly or annual plan and complete the purchase on this page. The agency account is activated once payment is confirmed."],
    ["当前状态", "Estado actual", "Status atual", "Current status"],
    ["正在读取会员状态", "Consultando membresía", "Consultando assinatura", "Loading membership status"],
    ["会员有效期、权益和订单与机构账号关联。", "La vigencia, los beneficios y los pedidos están vinculados a la cuenta de la agencia.", "A vigência, os benefícios e os pedidos estão vinculados à conta da agência.", "Membership term, benefits and orders are linked to the agency account."],
    ["机构级会员", "Membresía de agencia", "Assinatura da agência", "Agency membership"],
    ["进入工作台", "Entrar al sistema", "Entrar no sistema", "Open workspace"],
    ["会员方案", "Planes", "Planos", "Plans"],
    ["在本页选择月付或年付", "Elige plan mensual o anual", "Escolha mensal ou anual", "Choose monthly or annual"],
    ["30 天", "30 días", "30 dias", "30 days"],
    ["月度会员", "Plan mensual", "Plano mensal", "Monthly membership"],
    ["一次支付开通 30 天完整签证顾问工作流。", "Un pago habilita durante 30 días todo el flujo para asesores de visas.", "Um pagamento libera por 30 dias todo o fluxo para consultores de vistos.", "One payment unlocks the full visa consultant workflow for 30 days."],
    ["/ 30 天", "/ 30 días", "/ 30 dias", "/ 30 days"],
    ["材料识别、翻译与字段映射", "Lectura, traducción y mapeo de campos", "Leitura, tradução e mapeamento de campos", "Document reading, translation and field mapping"],
    ["客户补充链接与重点核查", "Enlace del cliente y revisión prioritaria", "Link do cliente e revisão prioritária", "Client form link and priority review"],
    ["浏览器辅助逐页填写", "Llenado página por página en el navegador", "Preenchimento página a página no navegador", "Page-by-page browser assistance"],
    ["立即购买月度会员", "Comprar plan mensual", "Comprar plano mensal", "Buy monthly membership"],
    ["365 天 · 推荐", "365 días · Recomendado", "365 dias · Recomendado", "365 days · Recommended"],
    ["年度会员", "Plan anual", "Plano anual", "Annual membership"],
    ["一次支付开通 365 天，按十个月计费。", "Un pago habilita 365 días por el precio de diez meses.", "Um pagamento libera 365 dias pelo preço de dez meses.", "One payment unlocks 365 days for the price of ten months."],
    ["/ 365 天", "/ 365 días", "/ 365 dias", "/ 365 days"],
    ["包含月度会员全部功能", "Incluye todas las funciones del plan mensual", "Inclui todos os recursos do plano mensal", "Includes every monthly-plan feature"],
    ["全年机构工作台使用权", "Acceso anual al sistema de la agencia", "Acesso anual ao sistema da agência", "Full-year agency workspace access"],
    ["团队协作与数据导出", "Colaboración del equipo y exportación", "Colaboração da equipe e exportação", "Team collaboration and data export"],
    ["相当于免两个月费用", "Equivale a dos meses sin costo", "Equivale a dois meses sem custo", "Equivalent to two months free"],
    ["立即购买年度会员", "Comprar plan anual", "Comprar plano anual", "Buy annual membership"],
    ["我代表当前机构确认已阅读并同意", "En nombre de la agencia, confirmo que he leído y acepto", "Em nome da agência, confirmo que li e aceito", "On behalf of the agency, I confirm that I have read and accept"],
    ["服务条款", "Términos del servicio", "Termos de serviço", "Terms of service"],
    ["隐私政策", "Aviso de privacidad", "Política de privacidade", "Privacy policy"],
    ["退款政策", "Política de reembolso", "Política de reembolso", "Refund policy"],
    ["退款与取消政策", "Política de reembolso y cancelación", "Política de reembolso e cancelamento", "Refund and cancellation policy"],
    ["当前方案为一次性购买固定使用期限，不自动续费。价格、币种和实际扣款金额以支付页面为准。", "Los planes se compran por un plazo fijo y no se renuevan automáticamente. El precio, la moneda y el cargo final se muestran en la página de pago.", "Os planos são comprados por prazo fixo e não têm renovação automática. O preço, a moeda e o valor final aparecem na página de pagamento.", "Plans are purchased for a fixed term and do not renew automatically. The payment page shows the price, currency and final charge."],
    ["运营主体法定资料尚未配置完整，真实支付入口保持关闭。", "Los datos legales del operador aún no están completos; los pagos reales permanecen deshabilitados.", "Os dados legais da operadora ainda não estão completos; os pagamentos reais permanecem desativados.", "The operator's legal profile is incomplete, so live payments remain disabled."],
    ["账户与帮助", "Cuenta y ayuda", "Conta e ajuda", "Account and help"],
    ["机构账号与会员状态", "Cuenta de agencia y membresía", "Conta da agência e assinatura", "Agency account and membership"],
    ["登录后，这里会读取当前机构账号、会员有效期和购买记录。案件资料仍只在操作台中处理。", "Al iniciar sesión, aquí verás la cuenta, la vigencia y las compras de la agencia. Los expedientes se gestionan únicamente en el sistema.", "Após entrar, você verá aqui a conta, a vigência e as compras da agência. Os casos continuam sendo tratados apenas no sistema.", "After signing in, this page shows the agency account, membership term and purchase history. Case data remains in the workspace."],
    ["登录或进入操作台", "Iniciar sesión o abrir el sistema", "Entrar ou abrir o sistema", "Sign in or open workspace"],
    ["从档案到 DS-160 初稿", "Del expediente al borrador DS-160", "Do caso ao rascunho DS-160", "From case to DS-160 draft"],
    ["当前流程包括档案、资料、整理、字段核查、待确认项、风险复核和 DS-160 初稿。", "El flujo incluye expediente, documentos, organización, revisión de campos, pendientes, riesgos y borrador DS-160.", "O fluxo inclui caso, documentos, organização, revisão de campos, pendências, riscos e rascunho DS-160.", "The workflow covers case setup, documents, organisation, field review, pending items, risk review and the DS-160 draft."],
    ["联系支持", "Contactar soporte", "Falar com o suporte", "Contact support"],
    ["查看使用说明与条款", "Ver instrucciones y términos", "Ver instruções e termos", "View instructions and terms"],
    ["会员购买说明", "Información de compra", "Informações da compra", "Purchase information"],
    ["香港运营主体资料尚未配置完整", "El perfil del operador de Hong Kong aún no está completo", "O perfil da operadora de Hong Kong ainda não está completo", "The Hong Kong operator profile is incomplete"],
    ["联系我们", "Contacto", "Contato", "Contact us"],
    ["首页", "Inicio", "Início", "Home"],
    ["请先登录机构账号", "Inicia sesión con la cuenta de la agencia", "Entre com a conta da agência", "Sign in with the agency account"],
    ["登录后才能创建订单，会员权益会绑定到当前机构。", "Debes iniciar sesión para crear un pedido. La membresía quedará vinculada a la agencia actual.", "É preciso entrar para criar um pedido. A assinatura ficará vinculada à agência atual.", "Sign in to create an order. Membership will be linked to the current agency."],
    ["尚未登录", "Sin sesión", "Não conectado", "Signed out"],
    ["登录后购买", "Inicia sesión para comprar", "Entre para comprar", "Sign in to buy"],
    ["、", ", ", ", ", ", "],
    ["及", " y ", " e ", " and "],
    ["月度和年度会员均为一次性购买的固定服务期限，不自动续费。功能、价格、币种、税费、实际扣款金额及退款条件，以本购买页面和", "Los planes mensual y anual se compran por un plazo fijo y no se renuevan automáticamente. Las funciones, el precio, la moneda, los impuestos, el cargo final y las condiciones de reembolso se rigen por esta página y la ", "Os planos mensal e anual são comprados por prazo fixo e não têm renovação automática. Recursos, preço, moeda, impostos, valor final e condições de reembolso seguem esta página e a ", "Monthly and annual memberships are fixed-term, one-time purchases with no automatic renewal. Features, price, currency, taxes, final charge and refund terms are governed by this page and the "],
    ["为准。", ".", ".", "."],
    ["为管理机构账户、会员、订单和服务安全，WestoryVisa 会处理必要的账户及交易记录；案件材料的处理范围、用途和保存方式请参阅", "Para administrar cuentas, membresías, pedidos y la seguridad del servicio, WestoryVisa procesa los registros necesarios de cuenta y transacción. Consulta el alcance, uso y conservación de los expedientes en el ", "Para administrar contas, assinaturas, pedidos e a segurança do serviço, a WestoryVisa processa os registros necessários de conta e transação. Consulte o escopo, uso e retenção dos casos na ", "To manage agency accounts, memberships, orders and service security, WestoryVisa processes necessary account and transaction records. See the scope, use and retention of case data in the "],
    ["材料可能由云托管、文档识别、翻译、人工智能、邮件或技术支持服务商按照约定进行必要处理，部分处理可能涉及跨境传输。机构客户上传材料前，应取得所需授权或单独同意。", "Los documentos pueden ser procesados por proveedores de alojamiento, lectura documental, traducción, inteligencia artificial, correo o soporte técnico, y parte del tratamiento puede ser transfronterizo. La agencia debe obtener las autorizaciones necesarias antes de cargar documentos.", "Os documentos podem ser processados por fornecedores de hospedagem, leitura documental, tradução, inteligência artificial, e-mail ou suporte técnico, e parte do tratamento pode ser internacional. A agência deve obter as autorizações necessárias antes de enviar documentos.", "Documents may be processed by hosting, document-reading, translation, AI, email or technical-support providers, and some processing may cross borders. The agency must obtain the required authorisation before uploading documents."]
  ];

  const INDEX = { MX: 1, BR: 2, IN: 3 };
  const sourceText = new WeakMap();
  const sourceAttributes = new WeakMap();
  let serviceCode = selectedCode();
  let code = selectedLanguageCode();
  let locked = false;
  let applying = false;
  let observer = null;
  let controlsMounted = false;

  function selectedCode() {
    const params = new URLSearchParams(window.location.search);
    const query = String(params.get("country") || "").toUpperCase();
    let saved = "";
    try { saved = String(window.localStorage.getItem(SERVICE_COUNTRY_STORAGE_KEY) || "").toUpperCase(); } catch (_) {}
    if (COUNTRY_ORDER.includes(query)) return query;
    return COUNTRY_ORDER.includes(saved) ? saved : "CN";
  }

  function dictionary() {
    const index = INDEX[code];
    if (!index) return new Map();
    return new Map(COPY.map((row) => [row[0], row[index]]));
  }

  function preserveSpacing(value, translated) {
    const leading = value.match(/^\s*/)?.[0] || "";
    const trailing = value.match(/\s*$/)?.[0] || "";
    return `${leading}${translated}${trailing}`;
  }

  function dynamicTranslation(value) {
    const countryNames = {
      MX: { "中国": "China", "墨西哥": "México", "巴西": "Brasil", "印度": "India" },
      BR: { "中国": "China", "墨西哥": "México", "巴西": "Brasil", "印度": "Índia" },
      IN: { "中国": "China", "墨西哥": "Mexico", "巴西": "Brazil", "印度": "India" }
    }[code];
    const version = value.match(/^(中国|墨西哥|巴西|印度)版(?: · (.+))?$/);
    if (version) {
      const name = countryNames[version[1]];
      const detail = version[2] ? (dictionary().get(version[2]) || version[2]) : "";
      return code === "MX" ? `Versión ${name}${detail ? ` · ${detail}` : ""}`
        : code === "BR" ? `Versão ${name}${detail ? ` · ${detail}` : ""}`
          : `${name} version${detail ? ` · ${detail}` : ""}`;
    }
    const documentCount = value.match(/^客户文档库 · (\d+)$/);
    if (documentCount) return code === "MX" ? `Documentos · ${documentCount[1]}` : code === "BR" ? `Documentos · ${documentCount[1]}` : `Documents · ${documentCount[1]}`;
    const emptyOrganization = value.match(/^(.+) 还没有客户档案$/);
    if (emptyOrganization) return code === "MX" ? `${emptyOrganization[1]} aún no tiene expedientes de clientes`
      : code === "BR" ? `${emptyOrganization[1]} ainda não tem casos de clientes`
        : `${emptyOrganization[1]} does not have any client cases yet`;
    const registrationScope = value.match(/^注册后机构固定为(中国|墨西哥|巴西|印度)服务机构，只处理本国客户。$/);
    if (registrationScope) {
      const name = countryNames[registrationScope[1]];
      return code === "MX"
        ? `Al registrarse, la agencia queda vinculada a ${name} y solo atiende clientes de ese país.`
        : code === "BR"
          ? `Ao se cadastrar, a agência fica vinculada a ${name} e atende apenas clientes desse país.`
          : `After registration, the agency is tied to ${name} and handles clients from that country only.`;
    }
    const countryWorkspace = value.match(/^(中国|墨西哥|巴西|印度)机构工作台$/);
    if (countryWorkspace) {
      const name = countryNames[countryWorkspace[1]];
      return code === "MX" ? `Sistema para agencias de ${name}` : code === "BR" ? `Sistema para agências do ${name}` : `${name} agency workspace`;
    }
    const organizationSummary = value.match(/^(.+) · (.+)。当前账号只能访问本机构的(中国|墨西哥|巴西|印度)客户档案。$/);
    if (organizationSummary) {
      const name = countryNames[organizationSummary[3]];
      return code === "MX"
        ? `${organizationSummary[1]} · ${organizationSummary[2]}. Esta cuenta solo accede a los expedientes de ${name} de su agencia.`
        : code === "BR"
          ? `${organizationSummary[1]} · ${organizationSummary[2]}. Esta conta acessa apenas os casos do ${name} da própria agência.`
          : `${organizationSummary[1]} · ${organizationSummary[2]}. This account can access only its agency's ${name} cases.`;
    }
    const owner = value.match(/^负责人：(.+)$/);
    if (owner) return code === "MX" ? `Responsable: ${owner[1]}` : code === "BR" ? `Responsável: ${owner[1]}` : `Owner: ${owner[1]}`;
    const updated = value.match(/^更新于 (.+)$/);
    if (updated) {
      const when = dictionary().get(updated[1]) || updated[1];
      return code === "MX" ? `Actualizado ${when}` : code === "BR" ? `Atualizado ${when}` : `Updated ${when}`;
    }
    const pendingNow = value.match(/^(\d+) 项当前待补充$/);
    if (pendingNow) return code === "MX" ? `${pendingNow[1]} datos pendientes` : code === "BR" ? `${pendingNow[1]} dados pendentes` : `${pendingNow[1]} details pending`;
    const intakeMore = value.match(/^另有 (\d+) 项将在客户表单中显示$/);
    if (intakeMore) return code === "MX" ? `${intakeMore[1]} más aparecerán en el formulario del cliente` : code === "BR" ? `Outros ${intakeMore[1]} aparecerão no formulário do cliente` : `${intakeMore[1]} more will appear in the client form`;
    const pendingSummary = value.match(/^(\d+) 项等待顾问最终确认，(\d+) 项资料需要通过客户补充链接收集。$/);
    if (pendingSummary) return code === "MX"
      ? `${pendingSummary[1]} preguntas requieren confirmación final del asesor y ${pendingSummary[2]} datos deben recopilarse mediante el enlace del cliente.`
      : code === "BR"
        ? `${pendingSummary[1]} perguntas exigem confirmação final do consultor e ${pendingSummary[2]} dados devem ser coletados pelo link do cliente.`
        : `${pendingSummary[1]} questions need final consultant confirmation, and ${pendingSummary[2]} details must be collected through the client form link.`;
    const records = value.match(/^(\d+) 条记录$/);
    if (records) return code === "MX" ? `${records[1]} registros` : code === "BR" ? `${records[1]} registros` : `${records[1]} records`;
    const items = value.match(/^(\d+) 项$/);
    if (items) return code === "MX" ? `${items[1]} elementos` : code === "BR" ? `${items[1]} itens` : `${items[1]} items`;
    const sectionPending = value.match(/^(\d+) 项待处理$/);
    if (sectionPending) return code === "MX" ? `${sectionPending[1]} pendientes` : code === "BR" ? `${sectionPending[1]} pendentes` : `${sectionPending[1]} pending`;
    const missingOutside = value.match(/^还有 (\d+) 项资料字段不在条件问答列表中$/);
    if (missingOutside) return code === "MX"
      ? `${missingOutside[1]} datos pendientes no están en la lista de preguntas condicionales`
      : code === "BR"
        ? `${missingOutside[1]} dados pendentes não estão na lista de perguntas condicionais`
        : `${missingOutside[1]} missing details are outside the conditional question list`;
    const moreInForm = value.match(/^；另有 (\d+) 项会出现在客户补充表中。$/);
    if (moreInForm) return code === "MX" ? `; ${moreInForm[1]} más aparecerán en el formulario del cliente.` : code === "BR" ? `; outros ${moreInForm[1]} aparecerão no formulário do cliente.` : `; ${moreInForm[1]} more will appear in the client form.`;
    const combinedMoreInForm = value.match(/^(.*)；另有 (\d+) 项会出现在客户补充表中。$/);
    if (combinedMoreInForm) return code === "MX"
      ? `${combinedMoreInForm[1]}; ${combinedMoreInForm[2]} más aparecerán en el formulario del cliente.`
      : code === "BR"
        ? `${combinedMoreInForm[1]}; outros ${combinedMoreInForm[2]} aparecerão no formulário do cliente.`
        : `${combinedMoreInForm[1]}; ${combinedMoreInForm[2]} more will appear in the client form.`;
    const needsAttention = value.match(/^(\d+) 项需要关注$/);
    if (needsAttention) return code === "MX" ? `${needsAttention[1]} puntos requieren atención` : code === "BR" ? `${needsAttention[1]} itens exigem atenção` : `${needsAttention[1]} items need attention`;
    const nonBlocking = value.match(/^(\d+) 项已处理或无需阻塞当前流程$/);
    if (nonBlocking) return code === "MX" ? `${nonBlocking[1]} puntos resueltos o que no bloquean este flujo` : code === "BR" ? `${nonBlocking[1]} itens resolvidos ou que não bloqueiam este fluxo` : `${nonBlocking[1]} items resolved or not blocking this workflow`;
    const processed = value.match(/^(\d+) \/ (\d+) 已处理$/);
    if (processed) return code === "MX" ? `${processed[1]} / ${processed[2]} procesadas` : code === "BR" ? `${processed[1]} / ${processed[2]} processadas` : `${processed[1]} / ${processed[2]} processed`;
    const notConfigured = value.match(/^(.+) 未配置$/);
    if (notConfigured) return code === "MX" ? `${notConfigured[1]} no está configurado` : code === "BR" ? `${notConfigured[1]} não está configurado` : `${notConfigured[1]} is not configured`;
    const previewItems = value.match(/^(\d+) 项预览$/);
    if (previewItems) return code === "MX" ? `${previewItems[1]} datos en vista previa` : code === "BR" ? `${previewItems[1]} dados na visualização` : `${previewItems[1]} preview items`;
    const routeCoverage = value.match(/^已映射 (\d+) · 当前 (.+)$/);
    if (routeCoverage) {
      const routeName = dictionary().get(routeCoverage[2]) || routeCoverage[2];
      return code === "MX" ? `${routeCoverage[1]} mapeadas · Actual: ${routeName}` : code === "BR" ? `${routeCoverage[1]} mapeadas · Atual: ${routeName}` : `${routeCoverage[1]} mapped · Current: ${routeName}`;
    }
    const incompatibleBackend = value.match(/^当前地址连接的是后端 (.+)，本页面需要 (.+) 或更新版本。请打开启动脚本刚刚弹出的网页地址。$/);
    if (incompatibleBackend) return code === "MX"
      ? `Esta dirección está conectada al backend ${incompatibleBackend[1]}; esta página requiere ${incompatibleBackend[2]} o una versión posterior. Abre la dirección que mostró el script de inicio.`
      : code === "BR"
        ? `Este endereço está conectado ao backend ${incompatibleBackend[1]}; esta página requer ${incompatibleBackend[2]} ou versão posterior. Abra o endereço exibido pelo script de inicialização.`
        : `This address is connected to backend ${incompatibleBackend[1]}; this page requires ${incompatibleBackend[2]} or later. Open the address shown by the startup script.`;
    return "";
  }

  function translateValue(value) {
    if (code === "CN") return value;
    const trimmed = String(value || "").trim();
    if (!trimmed) return value;
    const translated = dictionary().get(trimmed) || dynamicTranslation(trimmed);
    return translated ? preserveSpacing(value, translated) : value;
  }

  function translateTextNode(node) {
    if (node.parentElement?.closest("[data-language-select]")) return;
    const current = node.nodeValue || "";
    const cached = sourceText.get(node);
    const original = cached && current === cached.rendered ? cached.original : current;
    const next = translateValue(original);
    if (node.nodeValue !== next) node.nodeValue = next;
    sourceText.set(node, { original, rendered: next });
  }

  function translateElement(element) {
    if (!(element instanceof Element)) return;
    const attributes = ["placeholder", "aria-label", "title", "alt"];
    if (element.matches?.('meta[name="description"]')) attributes.push("content");
    if (element.matches?.("#applicationCountry[readonly]")) attributes.push("value");
    if (!sourceAttributes.has(element)) sourceAttributes.set(element, {});
    const originals = sourceAttributes.get(element);
    attributes.forEach((name) => {
      if (!element.hasAttribute(name)) return;
      const current = element.getAttribute(name) || "";
      const cached = originals[name];
      const original = cached && current === cached.rendered ? cached.original : current;
      const next = translateValue(original);
      if (element.getAttribute(name) !== next) element.setAttribute(name, next);
      originals[name] = { original, rendered: next };
    });
  }

  function apply(root = document) {
    if (applying) return;
    applying = true;
    document.documentElement.lang = LANGUAGE_IDS[code];
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT);
    let node = root;
    while (node) {
      if (node.nodeType === Node.TEXT_NODE) translateTextNode(node);
      else translateElement(node);
      node = walker.nextNode();
    }
    updateLinks(root);
    applying = false;
  }

  function localisedHref(rawHref) {
    const href = String(rawHref || "");
    if (!href || href.startsWith("#") || /^(mailto:|tel:|javascript:)/i.test(href)) return href;
    try {
      const url = new URL(href, window.location.href);
      if (url.origin !== window.location.origin) return href;
      url.searchParams.delete("country");
      url.searchParams.set(LANGUAGE_PARAMETER, LANGUAGE_IDS[code]);
      return `${url.pathname}${url.search}${url.hash}`;
    } catch (_error) {
      return href;
    }
  }

  function updateLinks(root = document) {
    const links = root.querySelectorAll ? root.querySelectorAll("a[href]") : [];
    links.forEach((link) => {
      if (link.dataset.countrySelect || link.dataset.languageSelect) return;
      if (!link.dataset.countryOriginalHref) link.dataset.countryOriginalHref = link.getAttribute("href") || "";
      const next = localisedHref(link.dataset.countryOriginalHref);
      if (link.getAttribute("href") !== next) link.setAttribute("href", next);
    });
  }

  function languageHref(nextCode) {
    const url = new URL(window.location.href);
    url.searchParams.delete("country");
    url.searchParams.set(LANGUAGE_PARAMETER, LANGUAGE_IDS[nextCode]);
    return `${url.pathname}${url.search}${url.hash}`;
  }

  function languageButton(nextCode) {
    const control = document.createElement("a");
    control.className = `country-version-option${nextCode === code ? " active" : ""}`;
    control.dataset.languageSelect = LANGUAGE_IDS[nextCode];
    control.lang = LANGUAGE_IDS[nextCode];
    control.hreflang = LANGUAGE_IDS[nextCode];
    control.setAttribute("translate", "no");
    control.href = languageHref(nextCode);
    control.textContent = LANGUAGE_NAMES[nextCode];
    if (nextCode === code) control.setAttribute("aria-current", "true");
    return control;
  }

  function selectLanguage(value, options = {}) {
    const nextCode = languageCode(value);
    if (!nextCode) return false;
    const changed = code !== nextCode;
    code = nextCode;
    remember(LANGUAGE_STORAGE_KEY, LANGUAGE_IDS[code]);
    setUrlLanguage();
    renderControls();
    apply(document);
    if (changed && options.reload !== false) window.location.reload();
    return true;
  }

  function renderControls() {
    const copy = UI[code];
    document.querySelectorAll("[data-country-current]").forEach((element) => {
      const label = LANGUAGE_NAMES[code];
      if (element.textContent !== label) element.textContent = label;
      element.classList.add("active");
      element.title = copy.choose;
      element.setAttribute("aria-label", `${copy.choose}: ${label}`);
      element.setAttribute("lang", LANGUAGE_IDS[code]);
      element.setAttribute("translate", "no");
    });
    const bar = document.querySelector(".country-version-bar");
    if (bar) {
      bar.querySelector("strong").textContent = copy.choose;
      bar.setAttribute("aria-label", copy.choose);
      const options = bar.querySelector(".country-version-options");
      options.replaceChildren(...LANGUAGE_ORDER.map(languageButton));
    }
    const picker = document.querySelector(".country-picker");
    if (picker) {
      picker.querySelector("h2").textContent = copy.title;
      picker.querySelector("p").textContent = copy.body;
      picker.querySelector(".country-picker-close").setAttribute("aria-label", copy.close);
      picker.querySelector(".country-picker-grid").replaceChildren(
        ...LANGUAGE_ORDER.map(languageButton)
      );
    }
  }

  function closePicker() {
    document.querySelector(".country-picker")?.classList.remove("open");
    document.body.classList.remove("country-picker-open");
  }

  function openPicker() {
    const picker = document.querySelector(".country-picker");
    if (!picker) return;
    picker.classList.add("open");
    document.body.classList.add("country-picker-open");
    picker.querySelector(".country-version-option.active")?.focus();
  }

  function mountControls() {
    if (controlsMounted || !document.body) return;
    controlsMounted = true;
    const header = document.querySelector(".site-header");
    if (header) {
      const bar = document.createElement("div");
      bar.className = "country-version-bar";
      bar.setAttribute("role", "navigation");
      bar.innerHTML = "<strong></strong><div class=\"country-version-options\"></div>";
      header.insertAdjacentElement("afterend", bar);
    }

    const picker = document.createElement("div");
    picker.className = "country-picker";
    picker.innerHTML = `
      <button class="country-picker-backdrop" type="button"></button>
      <section class="country-picker-dialog" role="dialog" aria-modal="true">
        <header><div><h2></h2><p></p></div><button class="country-picker-close" type="button">×</button></header>
        <div class="country-picker-grid"></div>
      </section>`;
    picker.querySelector(".country-picker-backdrop").addEventListener("click", closePicker);
    picker.querySelector(".country-picker-close").addEventListener("click", closePicker);
    document.body.appendChild(picker);
    document.addEventListener("click", (event) => {
      const control = event.target.closest?.("[data-country-current]");
      if (!control) return;
      event.preventDefault();
      openPicker();
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") closePicker();
    });
    renderControls();
  }

  function setUrlLanguage() {
    const url = new URL(window.location.href);
    url.searchParams.delete("country");
    url.searchParams.set(LANGUAGE_PARAMETER, LANGUAGE_IDS[code]);
    window.history.replaceState({}, "", `${url.pathname}${url.search}${url.hash}`);
  }

  function activate(nextCode, options = {}) {
    const normalized = String(nextCode || "").toUpperCase();
    if (!COUNTRY_ORDER.includes(normalized) || (locked && normalized !== serviceCode)) return false;
    const changed = normalized !== serviceCode;
    serviceCode = normalized;
    remember(SERVICE_COUNTRY_STORAGE_KEY, serviceCode);
    renderControls();
    apply(document);
    if (changed && options.reload !== false) window.location.reload();
    return true;
  }

  function lock(nextCode) {
    locked = false;
    activate(nextCode, { reload: false });
    locked = true;
    renderControls();
  }

  function unlock() {
    locked = false;
    renderControls();
  }

  const api = {
    get code() { return serviceCode; },
    get locale() { return COUNTRIES[serviceCode].locale; },
    get country() { return { ...COUNTRIES[serviceCode] }; },
    activate,
    apply,
    lock,
    unlock,
    registerTranslations(rows) {
      (rows || []).forEach((row) => {
        if (Array.isArray(row) && row.length >= 4) COPY.push(row);
      });
      apply(document);
    },
    localisedHref,
    countries: COUNTRY_ORDER.map((item) => ({ ...COUNTRIES[item] }))
  };
  window.WestoryCountry = api;
  window.WestoryLanguage = {
    get locale() { return LANGUAGE_IDS[code]; },
    get name() { return LANGUAGE_NAMES[code]; },
    activate: selectLanguage,
    translate: translateValue,
    registerTranslations: api.registerTranslations
  };

  function start() {
    remember(SERVICE_COUNTRY_STORAGE_KEY, serviceCode);
    remember(LANGUAGE_STORAGE_KEY, LANGUAGE_IDS[code]);
    setUrlLanguage();
    mountControls();
    apply(document);
    observer = new MutationObserver((records) => {
      if (applying) return;
      let controlsAdded = false;
      records.forEach((record) => {
        if (record.type === "characterData") translateTextNode(record.target);
        if (record.type === "attributes") translateElement(record.target);
        record.addedNodes.forEach((node) => {
          apply(node);
          if (
            node.nodeType === Node.ELEMENT_NODE
            && (node.matches?.("[data-country-current]") || node.querySelector?.("[data-country-current]"))
          ) controlsAdded = true;
        });
      });
      if (controlsAdded) renderControls();
    });
    observer.observe(document.body, {
      childList: true,
      subtree: true,
      characterData: true,
      attributes: true,
      attributeFilter: ["placeholder", "aria-label", "title", "alt"]
    });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start, { once: true });
  else start();
})();
