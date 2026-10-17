# 九合一選情分析智能體（jiuhe-election-agent）

內嵌在**飛書**中的台灣選舉分析機器人。團隊成員在飛書裡用自然語言提問，
機器人調用本地知識庫 + 即時網路檢索，輸出以「當前態勢」為主線的 Markdown 分析。

主軸：2026 九合一選舉 22 縣市首長（投票日 2026-11-28）。

## 它能做什麼

- 「分析台北市選情」→ 完整縣市分析報告（態勢速覽→參選格局→近期動態→民調檢視→結構基礎→地方因素→態勢判斷→資料缺口）
- 「最近一週台中有什麼變化」→ 即時檢索 + 已入庫事件
- 「高雄歷屆市長得票對比」→ 本地歷史庫直接查（1997–2022 縣市長、2008–2024 總統、2016–2024 區域立委）
- `/report <縣市>` 一鍵完整報告、`/reset` 清空上下文、`/help` 說明
- 允許趨勢判斷與勝負預測，但必須標註依據與置信度

## 架構

```
飛書 ←WS長連接→ app/bot（事件/卡片/會話路由）
                    │
               app/agent（LLM 工具調用循環，DeepSeek/Qwen/GLM 可切換）
                    │
        ┌───────────┼────────────┬─────────────┐
     kb 工具      web 檢索      calc 計算     record_event
        │           │
   SQLite 知識庫（歷史得票/本屆候選人/民調/地方知識/事件）
        ▲
   pipeline/（中選會歷史庫、2026 登記名冊、TVBS 民調 → 入庫）
```

設計要點：所有數字由工具/SQL 產出，LLM 不口算；歷史庫預建、動態即時檢索；
每個判斷標 as_of 並附來源。

## 快速開始

```bash
pip install -r requirements.txt
cp .env.example .env   # 填 FEISHU_APP_ID/SECRET + LLM key
python -m pipeline.run_all   # 建庫（下載 ~110MB 中選會資料）
python -m app.main           # 啟動機器人（前台）
```

Windows 常開機器部署見 `deploy/windows.md`。

## 飛書自建應用設置

1. [open.feishu.cn](https://open.feishu.cn) → 開發者後台 → 創建「企業自建應用」
2. 添加應用能力「機器人」
3. 權限：`im:message`、`im:message:send_as_bot`、`im:chat:readonly`
4. 事件訂閱：添加 `接收消息`（im.message.receive_v1）——長連接模式下無需回調地址
5. 發布應用版本 → 把機器人拉進群或私聊

## 目錄

```
app/      機器人本體（bot 層 / agent 循環 / 工具 / SQLite 訪問層）
pipeline/ 數據管道（中選會歷史庫、候選人名冊、民調入庫）
prompts/  system prompt 與分析方法定義
data/     生成的知識庫與原始文件緩存（不入庫）
deploy/   Windows 部署腳本與說明
tests/    單元測試
```

## 局限

- TVBS 民調 PDF 解析是 best-effort；解析不出的原始文件留在 `data/cache/polls/` 供人工補錄
- 區域立委的選區維度未細分（同縣市多選區的候選人 join 是近似值），主軸縣市長不受影響
- 設了 `NEWS_DB_PATH`（指向新聞監測 tw-news-monitor-simple 的 `data/news.db`）時，`web_search` 先唯讀查這個新聞庫，再用即時檢索補充並去重；未設或檔案不存在則只走即時檢索
- 即時檢索預設走 Google 新聞 RSS（繁中台灣新聞，免 key），DuckDuckGo 備援；有 Tavily key 可切 `SEARCH_PROVIDER=tavily`
