# docs インデックス（日本語・軽量版）

最終更新: 2026-09-27（worker-luna が自動生成）

詳細版（各文書の目的・主要セクションまで3行ずつ要約）は [INDEX_ja.md](INDEX_ja.md) を参照。
本ファイルは全文書の一行インデックスのみを扱う軽量版。要約の正本は各文書本体。

`docs/` 配下の全文書 20 本の日本語一行インデックス。
文書を追加・改名したときは本ファイルも更新すること。

## 全体像・方針

- [ARCHITECTURE.md](ARCHITECTURE.md) — システム全体構成。モジュラーモノリス（単一デプロイ + 厳格な内部境界）、プロバイダ中立な Pydantic ドメインモデル、provenance 付きプロバイダ層。
- [DATA_POLICY.md](DATA_POLICY.md) — データの出所とライセンス方針。`YOWAYOWA_MODE=public` では `PERSONAL_ONLY` を遮断する規則と、新プロバイダ追加時のライセンス宣言義務。
- [OPERATOR_MODE.md](OPERATOR_MODE.md) — Full / Operator モードの定義。personal モードを主製品とし、認証スクレイピング・私的データソース・自身の口座に対するブローカー操作を許可する範囲を規定。
- [PRIVATE_OPERATOR_ROADMAP.md](PRIVATE_OPERATOR_ROADMAP.md) — プライベート/ファミリーオペレータのロードマップ。現在状態（DONE / REAL-SESSION BLOCKED / IN PROGRESS 等）を現コード基準で更新。

## データソース — 公開 API

- [EDINET.md](EDINET.md) — 金融庁 EDINET API v2 による日本の開示情報リサーチ。発見 → メタデータ → 財務ファクト取得の正規パス。
- [ESTAT.md](ESTAT.md) — 政府統計 e-Stat REST API。テーブル ID を決め打ちせず discovery から始める方針。
- [SEC.md](SEC.md) — 米国企業財務の SEC EDGAR XBRL 公式 API。正規化と戦略エンリッチメントへの流し込み。
- [CRYPTO.md](CRYPTO.md) — 暗号資産日次 OHLCV（P4-E フェーズ1）。キー不要の CoinGecko / Binance から provenance 付きで JSONL 永続化。
- [STOCKS.md](STOCKS.md) — 米株日次確定足（P4-F）。Alpaca Market Data API から provenance 付きで JSONL 永続化。

## データソース — 実機・認証系 / スクレイピング

- [JPX_DAILY_MARGIN.md](JPX_DAILY_MARGIN.md) — JPX 銘柄別信用取引残高（日次・P4-A）。形式確認済み・公開開始（2026-09-28）待ちの取り込み契約。
- [CREDIT_MARGIN.md](CREDIT_MARGIN.md) — 週次信用残（P4-C・稼働中）。Yahoo!ファイナンス + 株探の観測フォーマットと取り込み契約。JPX 日次の週次コンパニオン。
- [MS2_RSS.md](MS2_RSS.md) — MARKET SPEED II RSS バッチ quote パイプライン。オペレーター自身の楽天口座フィード。VPS→Windows は非通信の片方向経路。
- [RAKUTEN_WEB_SESSION.md](RAKUTEN_WEB_SESSION.md) — 楽天証券 Web セッション実機検証手順書（P1B read-side の Definition of Done）。URL/セレクタは実機未検証の初期仮定。

## エージェント運用・メッセージ

- [AGENT_ENVIRONMENT.md](AGENT_ENVIRONMENT.md) — 自律エージェント間で共有する Python ツール venv と検証環境の規約。並列 worktree 作業で検証結果を壊さないための取り決め。
- [AGENT_MESSAGE_BOARD.md](AGENT_MESSAGE_BOARD.md) — ChatGPT ↔ Hermes の軽量メッセージボード。コードレビュー指摘・デバッグ助言・regression 疑いのやり取り（ID/Priority/Status 付き）。
- [AUTONOMOUS_AGENT_HANDOFF.md](AUTONOMOUS_AGENT_HANDOFF.md) — Hermes（長期自律エージェント）への運用引き継ぎ文書。読む順序と現在の作業指針。

## 証跡・受入

- [BROKER_ACCEPTANCE_MATRIX.md](BROKER_ACCEPTANCE_MATRIX.md) — 楽天証券 Web の受入状態の一元管理。fixture-green（自動テスト）と code-complete（実機未実行）を意図的に分離。
- [P1C_EVIDENCE.md](P1C_EVIDENCE.md) — P1C（会社 IR 取得パイプライン）の実測証跡。実コミット・実行 worktree・検証環境を記録。
- [P1D_EVIDENCE.md](P1D_EVIDENCE.md) — P1D（認証付き private 情報源・メールボックス）のエビデンス。受入基準と検証結果。
- [RESEARCH_BRIEF.md](RESEARCH_BRIEF.md) — P5-A。ローカルに蓄積した証跡上の LLM リサーチブリーフと research Q&A（朝のブリーフ含む）。
