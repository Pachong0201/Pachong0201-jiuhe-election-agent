# Windows 部署（團隊常開機器）

機器人是 **WebSocket 長連接** 模式，只要能訪問 `open.feishu.cn`，
**不需要公網 IP、域名或反向代理**。需要 Python 3.10 以上（建議 3.11）。

> **同一個飛書應用只能有一個機器人在跑。** 有兩個進程同時連著時，飛書會把每條消息隨機推給其中一個。
> 上線前先確認測試用的實例（Devin 虛擬機或其他電腦）已經停掉。

## 1. 安裝

```powershell
cd <repo 目錄>
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
copy .env.example .env   # 用記事本編輯，填入 App ID/Secret 與 LLM key
```

## 2. 接入新聞監測的新聞庫（可選，建議）

在 `.env` 加一行，填新聞監測主目錄下 `data\news.db` 的完整路徑（不加引號、反斜線照寫）：

```
NEWS_DB_PATH=D:\WXWorkLocal\TW News-Monitor111\tw-news-monitor-simple\data\news.db
```

機器人只讀這個檔案，不會寫入，也不影響新聞監測的計劃任務；新聞監測每 30 分鐘入庫的新文章，下一次檢索就能查到。
沒設或檔案不存在時，機器人自動退回只用 Google 新聞。

## 3. 建知識庫

```powershell
.\.venv\Scripts\python -m pipeline.run_all
```

會下載約 110MB 的中選會 votedata.zip 並入庫（一次性，幾分鐘）。
也可以直接把現成的 `kb.sqlite3` 放到 `data\` 目錄下，跳過這一步。

## 4. 自檢

```powershell
.\.venv\Scripts\python -m app.check
```

逐項檢查 `.env`、知識庫、新聞監測庫、飛書憑證、大模型 API、Google 新聞，有 FAIL 先修。
- **Google 新聞 WARN**（機器連不上 Google）：在 `.env` 設 `SEARCH_PROVIDER=none`，只用新聞監測庫，
  不然每次檢索都要等連線逾時；或者給機器配好代理（`HTTPS_PROXY=...` 也可以寫進 `.env`）。

## 5. 手動跑一次

```powershell
.\.venv\Scripts\python -m app.main
```

在飛書裡給機器人發 `/help`，再發「高雄选情分析」，確認進度卡片、摘要卡片、Word 附件都收到。Ctrl+C 停止。

## 6. 開機自啟（任務計劃程序）

```powershell
schtasks /create /tn "JiuheElectionBot" /sc onstart /delay 0001:00 /ru <你的帳號> /rp ^
  /tr "powershell -ExecutionPolicy Bypass -WindowStyle Hidden -File <repo>\deploy\run.ps1"
schtasks /run /tn "JiuheElectionBot"
```

- 用登錄 Windows 的帳號運行（`/rp` 會提示輸入密碼），不要用 SYSTEM：SYSTEM 拿不到該帳號的代理設置和網路盤。
- `run.ps1` 會在機器人退出後 30 秒自動重啟；`.env` 缺必填項時直接退出，不會反覆重啟。
- 日誌在 `data\bot.log`（UTF-8，超過 10MB 自動輪換，保留 5 份）。每份報告完成會寫一行 `report done: ... docx=uploaded`。
- 停止：`schtasks /end /tn "JiuheElectionBot"`。

## 故障排查：機器人不回應

1. 在飛書發 `/status`：有回覆說明機器人在線，會顯示飛書連線狀態、收到消息數、最近錯誤。
2. 沒回覆時，在 repo 目錄運行 `.\.venv\Scripts\python -m app.doctor`，它只讀本機 `data\bot.log`，
   會直接告訴你：機器人有沒有在跑、是否開了多個實例、本次啟動後有沒有收到消息、最近的錯誤。
3. 常見原因：
   - **多個實例**：前台窗口和計劃任務同時在跑，或別的電腦也在跑同一個飛書應用。只保留一個。
   - **前台窗口被點了一下**：窗口標題變成「選擇」時程序會暫停。新版本已自動關閉這個模式；舊版本按 Esc 恢復。
   - **卡死**：新版本有看門狗，飛書連線線程 3 分鐘沒反應就自動退出，由 `run.ps1` 30 秒後重啟；
     同一對話的分析超過 30 分鐘還沒結束，新的請求可以直接接手。
   - **電腦睡眠**：在電源設置裡把「睡眠」改為「從不」。

## 7. 數據更新

```powershell
.\.venv\Scripts\python -m pipeline.run_all --skip-history
```

建議每週跑一次（刷新民調庫和候選人名冊），可同樣掛到任務計劃程序。

## 8. 更新代碼

```powershell
schtasks /end /tn "JiuheElectionBot"
git pull
.\.venv\Scripts\pip install -r requirements.txt
schtasks /run /tn "JiuheElectionBot"
```
