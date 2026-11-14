# 実測: Hyperliquid テストネット API ページの UI 構造（2026-11-02 11:58 JST）

手順書 `docs/HL_EXEC_APPROVEAGENT_RUNBOOK.md` の根拠。ログインせずに公開ページの DOM を読んだだけ（入力・送信・鍵生成はしていない）。

## 対象

- URL: `https://app.hyperliquid-testnet.xyz/API`
- 取得方法: ブラウザで開き、`document.body.innerText` と該当フォームの `outerHTML` を読取（クリックは「Generate パネルの表示」のみ、送信なし）

## 画面上の文言（そのまま）

```
API
API wallets (also known as agent wallets) can perform actions on behalf of an account
without having withdrawal permissions. The account's public address must be used for info requests.

Generate | Connect

API Wallet Name | API Wallet Address | Valid Until | Action
```

→ **出金権限なし**がUI上も明記されている（設計§2.1-2 と一致）。

## 承認フォームの実DOM（抜粋・属性のみ）

```html
<div style="display: grid; grid-template-columns: 1fr 1fr auto; gap: 8px;">
  <div><input type="text" placeholder="The name for the API wallet, e.g. my_api_wallet" value=""></div>
  <div>
    <input type="text" placeholder="Address of API wallet, e.g. 0x123..." value="">
    <div><a style="font-size: 12px;">Generate</a></div>
  </div>
  <button color="primary" type="button">Connect</button>
</div>
```

読み取れること:

1. 承認フォームは **「名前」＋「APIウォレットのアドレス」＋ Connect ボタン**の1行。**アドレスを外から入力できる** ＝ システム側で生成した鍵のアドレスを、マスターが貼り付けて承認できる（秘密鍵を画面に出さない経路が存在する）。
2. アドレス欄の右にある **`Generate`** はブラウザ内で鍵を生成するリンク（押すと秘密鍵が画面に出る経路と推定／鍵生成を避けるため実測せず）。本件では**押さない**。
3. 一覧テーブルは `API Wallet Name / API Wallet Address / Valid Until / Action` の4列。承認済みが無い現在は空行のみ。

## 承認状況の読み取り確認（/info のみ・鍵不要）

`scripts/hl_exec_phase2/check_agents.py` の実測（2026-11-02 11:59 JST）:

```
POST /info extraAgents: HTTP 200 -> []
POST /info userRole:    HTTP 200 -> {"role": "user"}
```

→ 承認済みエージェントは現在 **0件**（未承認）。手順書の完了確認は `extraAgents` が非空になることで判定できる。
