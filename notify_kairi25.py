# notify_kairi25.py
# -*- coding: utf-8 -*-
"""
25日移動平均から-20%乖離（終値 < SMA25 * 0.8）の銘柄をチェックし、
コンソール表示／Slack Webhook／メールで通知する簡易スクリプト。
- 実行例:  python notify_kairi25.py --tickers tickers.txt --slack_webhook https://hooks.slack.com/services/XXX/YYY/ZZZ
- 日本株は「.T」サフィックス（例：7203.T）を付ける
- 15:10 JST など引け後に1日1回実行する想定です（cronやタスクスケジューラを使用）
"""
import argparse
import datetime as dt
import sys
from zoneinfo import ZoneInfo

import pandas as pd

try:
    import yfinance as yf
except Exception as e:
    print("yfinance のインストールが必要です: pip install yfinance pandas", file=sys.stderr)
    raise

import json
import smtplib
from email.mime.text import MIMEText
from urllib.request import Request, urlopen


def read_tickers(path: str) -> list[str]:
    with open(path, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip() and not line.strip().startswith("#")]


def fetch_prices(tickers: list[str], period: str = "90d") -> pd.DataFrame:
    # yfinanceは一括取得が安定
    data = yf.download(tickers, period=period, interval="1d", auto_adjust=True, group_by="ticker", progress=False)
    # 単一銘柄と複数銘柄で階層が変わるので正規化
    frames = []
    for t in tickers:
        try:
            df = data[t].copy()
        except Exception:
            # 単一銘柄時: カラムが1階層
            df = data.copy()
        df = df[['Close']].rename(columns={'Close': 'close'})
        df['ticker'] = t
        frames.append(df.reset_index())
    all_df = pd.concat(frames, ignore_index=True)
    return all_df


def compute_sma25_signals(df: pd.DataFrame) -> pd.DataFrame:
    # 日付ごと・銘柄ごとにSMA25を計算
    out = []
    for t, g in df.groupby('ticker'):
        g = g.sort_values('Date').copy()
        g['sma25'] = g['close'].rolling(25, min_periods=25).mean()
        g['kairi'] = (g['close'] / g['sma25'] - 1.0) * 100.0  # %
        g['signal'] = (g['close'] < g['sma25'] * 0.8)  # -20%乖離
        out.append(g)
    res = pd.concat(out, ignore_index=True)
    return res


def format_alerts(res: pd.DataFrame) -> str:
    jst = ZoneInfo("Asia/Tokyo")
    today = dt.datetime.now(jst).strftime("%Y-%m-%d %H:%M %Z")
    latest = res.sort_values(['ticker', 'Date']).groupby('ticker').tail(1)
    hit = latest[latest['signal']].copy()
    lines = [f"【-20% 乖離アラート】{today}"]
    if hit.empty:
        lines.append("該当なし")
    else:
        for _, r in hit.iterrows():
            d = pd.to_datetime(r['Date']).date().isoformat()
            k = f"{r['kairi']:.2f}%" if pd.notnull(r['kairi']) else "N/A"
            sma = f"{r['sma25']:.2f}" if pd.notnull(r['sma25']) else "N/A"
            lines.append(f"{r['ticker']}  日付:{d}  終値:{r['close']:.2f}  SMA25:{sma}  乖離:{k}")
    return "\n".join(lines)


def send_slack(webhook_url: str, text: str):
    payload = json.dumps({"text": text}).encode("utf-8")
    req = Request(webhook_url, data=payload, headers={"Content-Type": "application/json"})
    with urlopen(req, timeout=10) as resp:
        _ = resp.read()


def send_email(smtp_host: str, smtp_port: int, from_addr: str, to_addr: str, subject: str, body: str,
               username: str | None = None, password: str | None = None, use_tls: bool = True):
    msg = MIMEText(body, _charset="utf-8")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to_addr
    s = smtplib.SMTP(smtp_host, smtp_port, timeout=10)
    try:
        if use_tls:
            s.starttls()
        if username and password:
            s.login(username, password)
        s.sendmail(from_addr, [to_addr], msg.as_string())
    finally:
        s.quit()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", required=True, help="監視する銘柄リストのテキストファイル（1行1銘柄）")
    ap.add_argument("--slack_webhook", help="Slack Incoming Webhook URL（任意）")
    ap.add_argument("--email_to", help="メール宛先（任意）")
    ap.add_argument("--email_from", help="メール送信元（任意）")
    ap.add_argument("--smtp_host", help="SMTPホスト（任意）")
    ap.add_argument("--smtp_port", type=int, default=587, help="SMTPポート（任意, 既定=587）")
    ap.add_argument("--smtp_user", help="SMTPユーザー（任意）")
    ap.add_argument("--smtp_pass", help="SMTPパスワード（任意）")
    args = ap.parse_args()

    tickers = read_tickers(args.tickers)
    if not tickers:
        print("銘柄がありません（tickersファイルを確認）", file=sys.stderr)
        sys.exit(2)

    raw = fetch_prices(tickers)
    res = compute_sma25_signals(raw)
    text = format_alerts(res)
    print(text)

    # Slack通知
    if args.slack_webhook:
        try:
            send_slack(args.slack_webhook, text)
            print("Slack通知を送信しました。")
        except Exception as e:
            print(f"Slack通知エラー: {e}", file=sys.stderr)

    # メール通知
    if args.email_to and args.email_from and args.smtp_host:
        try:
            send_email(
                smtp_host=args.smtp_host,
                smtp_port=args.smtp_port,
                from_addr=args.email_from,
                to_addr=args.email_to,
                subject="25日線 -20%乖離アラート",
                body=text,
                username=args.smtp_user,
                password=args.smtp_pass,
            )
            print("メール通知を送信しました。")
        except Exception as e:
            print(f"メール送信エラー: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
