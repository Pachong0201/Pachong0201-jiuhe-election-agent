import io
import unittest

from docx import Document

from app.bot.cards import report_summary
from app.bot.docx_report import markdown_to_docx
from app.bot.progress import Progress

SAMPLE = """# 高雄市長選情研判（截至 2026-10-06）

## 一、選情總體態勢

**研判：藍綠貼身肉搏，選情膠著。**

| 民調機構 | 調查時間 | 柯志恩 | 賴瑞隆 |
|---|---|---|---|
| ETtoday | 2026-09-11 | 43.8% | 43.5% |

## 二、兩黨候選人動向

### 民進黨：賴瑞隆
**觀點一：主動設定辯論戰場**
說明文字。
- 依據：10/5 自由時報

### 國民黨：柯志恩
**觀點一：啟動韓流催基本盤**
> 註：資料缺口
"""


class TestDocx(unittest.TestCase):
    def test_renders_headings_table_and_bold(self):
        doc = Document(io.BytesIO(markdown_to_docx(SAMPLE)))
        texts = [p.text for p in doc.paragraphs]
        self.assertIn("高雄市長選情研判（截至 2026-10-06）", texts)
        self.assertIn("研判：藍綠貼身肉搏，選情膠著。", texts)
        self.assertEqual(len(doc.tables), 1)
        self.assertEqual(len(doc.tables[0].rows), 2)  # separator row dropped
        self.assertEqual(doc.tables[0].cell(1, 2).text, "43.8%")


class TestSummary(unittest.TestCase):
    def test_summary_has_verdict_and_viewpoints(self):
        s = report_summary(SAMPLE)
        self.assertTrue(s.startswith("**研判："))
        self.assertIn("民進黨：賴瑞隆", s)
        self.assertIn("・觀點一：啟動韓流催基本盤", s)
        self.assertNotIn("說明文字", s)


class TestProgress(unittest.TestCase):
    def test_stage_marks(self):
        p = Progress("高雄市選情研判")
        p.on_tools(["race_candidates", "get_polls"])
        p.on_tools(["web_search", "web_search"])
        text = p.render()
        self.assertIn("✅ 查詢候選人名冊", text)
        self.assertIn("⏳ 檢索兩黨候選人近期新聞（已搜尋 2 次）", text)
        self.assertIn("⬜ 生成 Word 文件", text)
        p.finish()
        self.assertNotIn("⏳", p.render())
        self.assertNotIn("⬜", p.render())


if __name__ == "__main__":
    unittest.main()
