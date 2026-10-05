import csv
import io
import unittest

from pipeline.cec_history import decode_csv, decode_zip_name


class TestCecDecode(unittest.TestCase):
    def test_zip_name_big5(self):
        # '1997縣市長' encoded Big5 then mis-decoded as cp437
        big5 = "1997縣市長".encode("big5")
        mojibake = big5.decode("cp437")
        self.assertEqual(decode_zip_name(mojibake), "1997縣市長")

    def test_decode_csv_strip_quotes(self):
        raw = "'10,'004,'00,'000,'0000,'新竹縣\n".encode("utf-8")
        rows = decode_csv(raw)
        self.assertEqual(rows[0][-1], "新竹縣")
        self.assertEqual(rows[0][1], "004")

    def test_decode_csv_big5(self):
        buf = io.StringIO()
        csv.writer(buf).writerow(["09", "007", "連江縣"])
        raw = buf.getvalue().encode("big5")
        rows = decode_csv(raw)
        self.assertEqual(rows[0][2], "連江縣")


if __name__ == "__main__":
    unittest.main()
