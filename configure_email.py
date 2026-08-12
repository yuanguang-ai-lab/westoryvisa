#!/usr/bin/env python3
import getpass
import subprocess
import sys
from pathlib import Path

from env_config import ENV_PATH, update_env_file


LEGACY_KEYS = {
    "DOCFLOW_MAIL_MODE", "DOCFLOW_SMTP_HOST", "DOCFLOW_SMTP_PORT",
    "DOCFLOW_SMTP_SECURITY", "DOCFLOW_SMTP_USERNAME", "DOCFLOW_SMTP_PASSWORD",
    "DOCFLOW_SMTP_FROM_EMAIL", "DOCFLOW_SMTP_FROM_NAME",
}
SMTP_KEYS = {"SMTP_USERNAME", "SMTP_PASSWORD"}
MICROSOFT_KEYS = {
    "MICROSOFT_TENANT_ID", "MICROSOFT_CLIENT_ID", "MICROSOFT_CLIENT_SECRET",
    "MICROSOFT_REFRESH_TOKEN", "MICROSOFT_REFRESH_TOKEN_FILE", "MICROSOFT_SCOPES",
}


def required(prompt, secret=False, default=""):
    while True:
        suffix = f"（默认 {default}）" if default else ""
        value = getpass.getpass(prompt + suffix + "：") if secret else input(prompt + suffix + "：")
        value = value.strip() if not secret else value
        if value or default:
            return value or default
        print("此项不能为空，请重新填写。")


def choose(prompt, options):
    print(prompt)
    for key, label in options:
        print(f"  {key}. {label}")
    valid = {key for key, _ in options}
    while True:
        answer = input("请选择：").strip()
        if answer in valid:
            return answer
        print("请输入列表中的数字。")


def configure_smtp():
    host = required("SMTP 地址，例如 smtp.exmail.qq.com")
    security = choose("连接安全方式", [("1", "SSL（常用端口 465）"), ("2", "STARTTLS（常用端口 587）")])
    default_port = "465" if security == "1" else "587"
    port = required("SMTP 端口", default=default_port)
    from_email = required("发件邮箱")
    username = input("SMTP 用户名（直接回车则与发件邮箱相同）：").strip() or from_email
    password = required("SMTP 授权码（输入不会显示）", secret=True)
    from_name = input("发件人名称（默认 DocFlow DS-160）：").strip() or "DocFlow DS-160"
    update_env_file({
        "REGISTRATION_VERIFICATION": "email",
        "MAIL_PROVIDER": "smtp",
        "MAIL_FROM": from_email,
        "MAIL_FROM_NAME": from_name,
        "SMTP_HOST": host,
        "SMTP_PORT": port,
        "SMTP_SECURE": "true" if security == "1" else "false",
        "SMTP_USERNAME": username,
        "SMTP_PASSWORD": password,
    }, remove_keys=MICROSOFT_KEYS | LEGACY_KEYS)


def configure_microsoft():
    account_type = choose(
        "Microsoft 账号类型",
        [("1", "个人 Outlook.com / Hotmail / Live"), ("2", "Microsoft 365 工作或学校账号")],
    )
    from_email = required("发件邮箱")
    if account_type == "1":
        tenant_id = input("Tenant ID（默认 consumers）：").strip() or "consumers"
        host = "smtp-mail.outlook.com"
    else:
        tenant_id = required("Microsoft Entra 租户 Tenant ID")
        host = "smtp.office365.com"
    client_id = required("应用 Client ID")
    client_secret = required("Client Secret（输入不会显示）", secret=True)
    refresh_token = getpass.getpass("Refresh Token（可留空，随后自动打开浏览器授权）：")
    from_name = input("发件人名称（默认 DocFlow DS-160）：").strip() or "DocFlow DS-160"
    updates = {
        "REGISTRATION_VERIFICATION": "email",
        "MAIL_PROVIDER": "microsoft_oauth2",
        "MAIL_FROM": from_email,
        "MAIL_FROM_NAME": from_name,
        "MICROSOFT_TENANT_ID": tenant_id,
        "MICROSOFT_CLIENT_ID": client_id,
        "MICROSOFT_CLIENT_SECRET": client_secret,
        "MICROSOFT_REFRESH_TOKEN": refresh_token,
        "SMTP_HOST": host,
        "SMTP_PORT": "587",
        "SMTP_SECURE": "false",
    }
    update_env_file(updates, remove_keys=SMTP_KEYS | LEGACY_KEYS)
    try:
        (Path(__file__).resolve().parent / "data" / "microsoft_refresh_token").unlink()
    except FileNotFoundError:
        pass
    if not refresh_token:
        print("\n即将打开 Microsoft 登录授权页。授权结果只写入本机 .env，不会显示令牌。")
        subprocess.run([sys.executable, "microsoft_oauth_setup.py"], check=True)


def main():
    print("配置 DocFlow 注册邮箱验证码\n")
    choice = choose(
        "选择邮件发送方式",
        [("1", "普通 SMTP（用户名 + SMTP 授权码）"), ("2", "Microsoft Outlook SMTP OAuth2")],
    )
    if choice == "1":
        configure_smtp()
    else:
        configure_microsoft()
    print(f"\n配置已安全保存到 {ENV_PATH.name}，文件权限为 600。")
    print("请关闭旧服务，再双击“启动完整版本.command”。")


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print("\n已取消配置。")
        raise SystemExit(1)
    except subprocess.CalledProcessError:
        print("\nMicrosoft 授权未完成。基础配置已保存，可稍后运行 microsoft_oauth_setup.py。")
        raise SystemExit(1)
