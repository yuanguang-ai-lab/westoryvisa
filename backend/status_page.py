"""Small dependency-free operations page owned by the backend."""

import html


def _text(value, fallback="—"):
    normalized = str(value or "").strip()
    return html.escape(normalized or fallback)


def _status_card(title, available, detail):
    state = "正常" if available else "未就绪"
    state_class = "ok" if available else "warn"
    return f"""
      <article class="service-card">
        <div class="service-card__top">
          <span class="service-card__icon"></span>
          <span class="badge badge--{state_class}">{state}</span>
        </div>
        <h3>{_text(title)}</h3>
        <p>{_text(detail)}</p>
      </article>
    """


def render_status_page(health, ocr):
    email = health.get("emailVerification") or {}
    translation = health.get("translation") or {}
    screen_agent = health.get("screenAgent") or {}
    registration = health.get("registrationVerification") or {}
    cards = "".join([
        _status_card(
            "数据库与 API",
            health.get("ok") is True,
            f"API Revision {health.get('apiRevision', '—')} · SQLite 已连接",
        ),
        _status_card(
            "文档识别 OCR",
            ocr.get("available") is True,
            ocr.get("message") or (
                f"{ocr.get('providerLabel') or ocr.get('service', '文档解析服务')}"
            ),
        ),
        _status_card(
            "邮件验证",
            email.get("configured") is True,
            email.get("message") or email.get("provider") or "尚未配置",
        ),
        _status_card(
            "翻译服务",
            translation.get("libreTranslate") is True
            or translation.get("ollamaFallback") is True,
            f"Provider: {translation.get('provider', 'auto')}",
        ),
        _status_card(
            "Screen Agent",
            screen_agent.get("available") is True,
            screen_agent.get("message") or screen_agent.get("mode") or "未启用",
        ),
        _status_card(
            "注册验证",
            registration.get("required") is not True
            or email.get("configured") is True,
            f"Mode: {registration.get('mode', 'none')}",
        ),
    ])
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="refresh" content="30">
  <title>WestoryVisa Backend</title>
  <style>
    :root {{
      color-scheme: dark;
      --bg: #090b0a;
      --panel: #121513;
      --panel-soft: #181c19;
      --line: #2a302c;
      --text: #f4f7f5;
      --muted: #9ca69f;
      --accent: #d8ff57;
      --ok: #77e89b;
      --warn: #f6ca68;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      min-height: 100vh;
      background:
        radial-gradient(circle at 10% -10%, rgba(216,255,87,.12), transparent 32rem),
        var(--bg);
      color: var(--text);
      font: 15px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    main {{ width: min(1120px, calc(100% - 40px)); margin: 0 auto; padding: 52px 0 70px; }}
    header {{ display: flex; justify-content: space-between; gap: 24px; align-items: flex-start; }}
    .brand {{ display: flex; align-items: center; gap: 10px; font-weight: 700; letter-spacing: -.02em; }}
    .brand-dot {{ width: 10px; height: 10px; border-radius: 50%; background: var(--accent); box-shadow: 0 0 18px var(--accent); }}
    .eyebrow {{ margin: 70px 0 12px; color: var(--accent); font-size: 12px; font-weight: 700; letter-spacing: .16em; text-transform: uppercase; }}
    h1 {{ margin: 0; max-width: 760px; font-size: clamp(42px, 7vw, 76px); line-height: .98; letter-spacing: -.065em; }}
    .lead {{ max-width: 680px; margin: 24px 0 0; color: var(--muted); font-size: 18px; }}
    .live {{
      display: inline-flex; align-items: center; gap: 8px; white-space: nowrap;
      border: 1px solid var(--line); border-radius: 999px; padding: 9px 14px;
      color: var(--ok); background: rgba(119,232,155,.06);
    }}
    .live::before {{ content: ""; width: 7px; height: 7px; border-radius: 50%; background: var(--ok); }}
    .summary {{
      display: grid; grid-template-columns: repeat(3, 1fr); gap: 1px;
      margin: 48px 0 22px; overflow: hidden; border: 1px solid var(--line);
      border-radius: 18px; background: var(--line);
    }}
    .summary div {{ padding: 22px; background: var(--panel); }}
    .summary span {{ display: block; color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: .08em; }}
    .summary strong {{ display: block; margin-top: 6px; font-size: 18px; }}
    .services {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; }}
    .service-card {{ min-height: 190px; padding: 22px; border: 1px solid var(--line); border-radius: 18px; background: linear-gradient(145deg, var(--panel-soft), var(--panel)); }}
    .service-card__top {{ display: flex; justify-content: space-between; align-items: center; }}
    .service-card__icon {{ width: 30px; height: 30px; border: 1px solid #3a423d; border-radius: 9px; background: linear-gradient(135deg, rgba(216,255,87,.18), transparent); }}
    .badge {{ padding: 4px 9px; border-radius: 999px; font-size: 11px; font-weight: 700; }}
    .badge--ok {{ color: var(--ok); background: rgba(119,232,155,.1); }}
    .badge--warn {{ color: var(--warn); background: rgba(246,202,104,.1); }}
    h3 {{ margin: 32px 0 7px; font-size: 17px; }}
    .service-card p {{ margin: 0; color: var(--muted); font-size: 13px; }}
    .endpoints {{ margin-top: 22px; padding: 26px; border: 1px solid var(--line); border-radius: 18px; background: var(--panel); }}
    .endpoints h2 {{ margin: 0 0 16px; font-size: 18px; }}
    .endpoint-grid {{ display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px; }}
    code {{ display: block; padding: 11px 13px; color: #dfe7e1; border: 1px solid var(--line); border-radius: 9px; background: #0c0e0d; }}
    footer {{ display: flex; justify-content: space-between; margin-top: 24px; color: var(--muted); font-size: 12px; }}
    footer a {{ color: var(--accent); text-decoration: none; }}
    @media (max-width: 760px) {{
      main {{ width: min(100% - 24px, 1120px); padding-top: 28px; }}
      header {{ flex-direction: column; }}
      .eyebrow {{ margin-top: 48px; }}
      .summary, .services, .endpoint-grid {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <main>
    <header>
      <div class="brand"><span class="brand-dot"></span>WestoryVisa Backend</div>
      <div class="live">API Online</div>
    </header>
    <p class="eyebrow">Operations overview</p>
    <h1>后端服务<br>运行状态</h1>
    <p class="lead">这是独立后端的轻量状态页，用于确认 API 和基础服务是否就绪。页面每 30 秒自动刷新，不展示客户资料或密钥。</p>

    <section class="summary" aria-label="后端摘要">
      <div><span>API Version</span><strong>{_text(health.get("apiVersion"))}</strong></div>
      <div><span>Authentication</span><strong>{_text(health.get("auth"))}</strong></div>
      <div><span>Architecture</span><strong>Standalone API</strong></div>
    </section>

    <section class="services" aria-label="服务状态">{cards}</section>

    <section class="endpoints">
      <h2>常用接口</h2>
      <div class="endpoint-grid">
        <code>GET /api/health</code>
        <code>GET /api/session</code>
        <code>GET /api/cases</code>
        <code>GET /api/ocr/health</code>
      </div>
    </section>

    <footer>
      <span>WestoryVisa · Backend revision {_text(health.get("apiRevision"))}</span>
      <a href="/api/health">查看 JSON 健康信息 →</a>
    </footer>
  </main>
</body>
</html>"""
