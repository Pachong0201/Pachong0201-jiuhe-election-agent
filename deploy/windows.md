# Windows 部署（團隊常開機器）

機器人是 **WebSocket 長連接** 模式——只要機器能上網訪問 `open.feishu.cn` 即可，
**不需要公網 IP / 域名 / 反向代理**。

## 1. 一次性安裝

```powershell
cd <repo 目錄>
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
copy .env.example .env   # 然後編輯填入 App ID/Secret 與 LLM key
```

## 2. 初始化數據庫

```powershell
.\.venv\Scripts\python -m pipeline.run_all
```

歷史庫會下載約 110MB 的中選會 votedata.zip 並入庫（一次性，約 1–2 分鐘）。

## 3. 手動跑起來驗證

```powershell
.\.venv\Scripts\python -m app.main
```

在飛書裡給機器人發 `/help`，能收到回覆就通了。Ctrl+C 停止。

## 4. 開機自啟（任務計劃程序）

```powershell
schtasks /create /tn "JiuheElectionBot" /sc onstart /ru SYSTEM /rl HIGHEST ^
  /tr "powershell -ExecutionPolicy Bypass -WindowStyle Hidden -File <repo>\deploy\run.ps1"
schtasks /run /tn "JiuheElectionBot"
```

或者用「任務計劃程序」GUI：觸發器=系統啟動時，操作=啟動 `run.ps1`。
日誌在 `data\bot.log`（`Tee-Object` 追加寫）。

崩潰自動重啟：run.ps1 退出碼非 0 時任務會結束；如需守護，可用 NSSM
（`nssm install JiuheElectionBot "python" "-m app.main"`）註冊為 Windows 服務。

## 5. 數據更新

```powershell
.\.venv\Scripts\python -m pipeline.run_all --skip-history
```

建議每週跑一次（民調庫+候選人名冊刷新），可同樣掛到任務計劃程序。
