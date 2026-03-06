"""
학교종 Discord 봇 — 진입점
"""
import os

from app.config import _BASE_DIR


def main() -> None:
    # .env 파일 로드 (exe 옆 또는 소스 디렉토리)
    _env_file = _BASE_DIR / ".env"
    try:
        from dotenv import load_dotenv
        load_dotenv(_env_file)
    except ImportError:
        pass

    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        # 첫 실행 시 토큰 입력 후 .env 에 저장
        token = input("Discord 봇 토큰을 입력하세요: ").strip()
        if token:
            _env_file.write_text(f"DISCORD_TOKEN={token}\n", encoding="utf-8")
            print(f".env 저장 완료 ({_env_file})")
        else:
            raise SystemExit("토큰이 없습니다.")

    from app.bot.client import bot
    bot.run(token, log_handler=None)


if __name__ == "__main__":
    main()
