import csv
import json
import os
import ssl
import sys
import traceback
import urllib.request
from dataclasses import dataclass
from html import escape
from http.server import BaseHTTPRequestHandler, HTTPServer
from io import StringIO, TextIOWrapper
from typing import Any
from urllib.parse import quote


def load_dotenv_simple(path: str = ".env") -> None:
    """
    Zero-dependency .env loader.
    - Only sets variables that are not already in os.environ
    - Supports KEY=VALUE, ignores blank lines and # comments
    - Strips surrounding single/double quotes
    """
    if not os.path.exists(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip()
                if not k or k in os.environ:
                    continue
                if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
                    v = v[1:-1]
                os.environ[k] = v
    except Exception:
        # Keep server usable even if .env parsing fails
        return


def safe_float(x: Any) -> float | None:
    try:
        if x is None:
            return None
        s = str(x).strip()
        if s == "":
            return None
        s = s.replace(",", "")
        return float(s)
    except Exception:
        return None


@dataclass
class CsvProfile:
    rows: int
    cols: int
    headers: list[str]
    missing_by_col: dict[str, int]
    numeric_cols: list[str]
    numeric_stats: dict[str, dict[str, float]]
    inferred_index_cols: list[str]
    price_col: str | None
    avg_price_by: dict[str, list[tuple[str, float]]]
    kpi: dict[str, float]
    trend_days_left: list[tuple[int, float]]
    sample_rows: list[dict[str, str]]


def profile_csv(text: str, max_sample_rows: int = 30) -> CsvProfile:
    f = StringIO(text)
    reader = csv.DictReader(f)
    headers = reader.fieldnames or []
    inferred_index_cols: list[str] = []
    # Common pattern: a leading unnamed column (CSV saved with index)
    headers = [h if h is not None else "" for h in headers]
    if headers and headers[0].strip() == "":
        inferred_index_cols.append(headers[0])
    missing_by_col = {h: 0 for h in headers}
    numeric_values: dict[str, list[float]] = {h: [] for h in headers}
    sample_rows: list[dict[str, str]] = []

    price_col = "price" if "price" in headers else None
    days_left_col = "days_left" if "days_left" in headers else None
    # For avg price by categorical dimensions
    avg_price_by: dict[str, dict[str, list[float]]] = {}
    candidate_dims = ["airline", "source_city", "destination_city", "class", "stops", "departure_time", "arrival_time"]
    for dim in candidate_dims:
        if dim in headers and price_col is not None:
            avg_price_by[dim] = {}

    rows = 0
    prices: list[float] = []
    # trend: avg price by days_left (binned)
    # 0-3,4-7,8-14,15-21,22-30,31+
    bins = [(0, 3), (4, 7), (8, 14), (15, 21), (22, 30), (31, 10 ** 9)]
    bin_sum = [0.0 for _ in bins]
    bin_cnt = [0 for _ in bins]

    for row in reader:
        rows += 1
        # remove unnamed index column from row
        if headers and headers[0].strip() == "":
            row.pop(headers[0], None)
        effective_headers = [h for h in headers if h.strip() != ""]

        if len(sample_rows) < max_sample_rows:
            sample_rows.append({k: (row.get(k) or "") for k in effective_headers})

        for h in effective_headers:
            v = (row.get(h) or "").strip()
            if v == "":
                missing_by_col[h] += 1
            fv = safe_float(v)
            if fv is not None:
                numeric_values[h].append(fv)

        if price_col is not None:
            price_v = safe_float((row.get(price_col) or "").strip())
            if price_v is not None:
                prices.append(price_v)
                for dim, buckets in avg_price_by.items():
                    key = (row.get(dim) or "").strip()
                    if key == "":
                        continue
                    buckets.setdefault(key, []).append(price_v)
                if days_left_col is not None:
                    dl = safe_float((row.get(days_left_col) or "").strip())
                    if dl is not None:
                        dli = int(dl)
                        for i, (a, b) in enumerate(bins):
                            if a <= dli <= b:
                                bin_sum[i] += price_v
                                bin_cnt[i] += 1
                                break

    numeric_cols: list[str] = []
    numeric_stats: dict[str, dict[str, float]] = {}
    effective_headers = [h for h in headers if h.strip() != ""]
    for h in effective_headers:
        vals = numeric_values[h]
        # heuristic: consider numeric if enough non-empty numeric values
        if len(vals) >= max(3, int(rows * 0.3)) and rows > 0:
            numeric_cols.append(h)
            vals_sorted = sorted(vals)
            n = len(vals_sorted)
            mean = sum(vals_sorted) / n
            var = sum((x - mean) ** 2 for x in vals_sorted) / max(1, n - 1)
            std = var ** 0.5
            numeric_stats[h] = {
                "count": float(n),
                "min": float(vals_sorted[0]),
                "max": float(vals_sorted[-1]),
                "mean": float(mean),
                "std": float(std),
            }

    avg_price_by_out: dict[str, list[tuple[str, float]]] = {}
    for dim, buckets in avg_price_by.items():
        pairs: list[tuple[str, float]] = []
        for k, vals in buckets.items():
            if len(vals) >= 10:
                pairs.append((k, sum(vals) / len(vals)))
        pairs.sort(key=lambda kv: kv[1], reverse=True)
        if pairs:
            avg_price_by_out[dim] = pairs

    kpi: dict[str, float] = {}
    if prices:
        ps = sorted(prices)
        n = len(ps)
        kpi["avg_price"] = sum(ps) / n
        kpi["min_price"] = ps[0]
        kpi["max_price"] = ps[-1]
        kpi["p50_price"] = ps[n // 2]

    trend_days_left: list[tuple[int, float]] = []
    for i, (a, b) in enumerate(bins):
        if bin_cnt[i] > 0:
            mid = a if b >= 10 ** 9 else (a + b) // 2
            trend_days_left.append((mid, bin_sum[i] / bin_cnt[i]))

    return CsvProfile(
        rows=rows,
        cols=len(effective_headers),
        headers=effective_headers,
        missing_by_col=missing_by_col,
        numeric_cols=numeric_cols,
        numeric_stats=numeric_stats,
        inferred_index_cols=inferred_index_cols,
        price_col=price_col,
        avg_price_by=avg_price_by_out,
        kpi=kpi,
        trend_days_left=trend_days_left,
        sample_rows=sample_rows,
    )


def rule_insights(p: CsvProfile) -> list[str]:
    lines: list[str] = []
    lines.append(f"行数: {p.rows}；列数: {p.cols}")
    if p.inferred_index_cols:
        lines.append("检测到疑似索引列：已自动忽略（CSV 里最前面的空列）。")

    missing_total = sum(p.missing_by_col.values())
    if missing_total == 0:
        lines.append("缺失值: 0（很好）")
    else:
        top = sorted(p.missing_by_col.items(), key=lambda kv: kv[1], reverse=True)[:5]
        top = [(k, v) for k, v in top if v > 0]
        if top:
            lines.append("缺失值最多的列(Top5): " + ", ".join([f"{k}({v})" for k, v in top]))

    if p.numeric_cols:
        # pick 1-3 most variable columns by std
        var_cols = sorted(
            p.numeric_cols,
            key=lambda c: p.numeric_stats.get(c, {}).get("std", 0.0),
            reverse=True,
        )[:3]
        lines.append("波动最大的数值列(按标准差): " + ", ".join(var_cols))
        # outlier-ish hint
        ratios: list[tuple[str, float]] = []
        for c in p.numeric_cols:
            st = p.numeric_stats.get(c, {})
            mean = st.get("mean", 0.0)
            mx = st.get("max", 0.0)
            if mean not in (0.0,) and mx and mean:
                ratios.append((c, mx / mean))
        ratios.sort(key=lambda kv: kv[1], reverse=True)
        if ratios and ratios[0][1] >= 10:
            lines.append(f"异常值提示: {ratios[0][0]} 的 max/mean 很高（可能存在极端值）")
    else:
        lines.append("数值列: 0（可能都是文本/类别列）")

    if p.price_col and p.avg_price_by:
        lines.append("")
        lines.append("价格（price）相关：")
        for dim, pairs in p.avg_price_by.items():
            top = pairs[:3]
            low = list(reversed(pairs[-3:])) if len(pairs) >= 3 else []
            if top:
                lines.append(f"- 按 {dim} 的均价 Top3: " + ", ".join([f"{k}({v:.0f})" for k, v in top]))
            if low:
                lines.append(f"- 按 {dim} 的均价 Low3: " + ", ".join([f"{k}({v:.0f})" for k, v in low]))

    lines.append("")
    lines.append("你可以试试问：")
    lines.append(" - 哪家 airline 最贵/最便宜？为什么？")
    lines.append(" - 提前 days_left 越多，price 是否越低？给一个可执行建议。")
    lines.append(" - stops=zero vs one 的价格差大吗？")
    lines.append(" - source_city=Delhi 到 destination_city=Mumbai 哪个时间段更便宜？")
    return lines


def openai_answer(question: str, p: CsvProfile) -> tuple[bool, str]:
    from zhipuai import ZhipuAI
    import time
    client = ZhipuAI(api_key="4ff7309af7b648bf9978029246a04c4d.6zQwsrUtNt2BqDoP")

    def get_completion(prompt: str, content: str, history=None):
        if history is None:
            history = []
        history.append({"role": "system", "content": prompt})
        history.append({"role": "user", "content": content})
        response = client.chat.asyncCompletions.create(
            model="glm-4-flash",
            messages=history,
        )
        task_id = response.id
        task_status = ''
        get_cnt = 0
        while task_status != 'SUCCESS' and task_status != 'FAILED' and get_cnt <= 40:
            result_response = client.chat.asyncCompletions.retrieve_completion_result(id=task_id)
            task_status = result_response.task_status
            time.sleep(.5)
            get_cnt += 1
        content = result_response.choices[0].message.content
        history.append({"role": "assistant", "content": content})
        return content, history

    schema_lines = [f"- {h}" for h in p.headers[:60]]
    sample_csv = StringIO()
    if p.sample_rows:
        w = csv.DictWriter(sample_csv, fieldnames=p.headers)
        w.writeheader()
        for r in p.sample_rows[:30]:
            w.writerow(r)
    # payload = {
    #     "model": model,
    #     "input": [
    #         {
    #             "role": "system",
    #             "content": (
    #                 "你是数据分析助理。你会得到一个 CSV 数据集的列名和部分样例行。"
    #                 "请用中文回答问题，先给结论，再给依据/建议。"
    #                 "如果信息不足，说明缺什么列或需要什么统计，再给出你能给的最佳推断。"
    #             ),
    #         },
    #         {
    #             "role": "user",
    #             "content": (
    #                 f"数据集概况：rows={p.rows}, cols={p.cols}\n"
    #                 f"列名：\n{chr(10).join(schema_lines)}\n\n"
    #                 f"样例行CSV：\n{sample_csv.getvalue()}\n\n"
    #                 f"问题：{question}"
    #             ),
    #         },
    #     ],
    # }

    prompt = (
        "你是数据分析助理。用户会给你一个 CSV 数据集的 schema 和部分样例行。"
        "你要用中文回答问题，给出清晰结论，并尽量给出可执行建议。"
        "如果信息不足，先说需要哪一列/哪种统计，再给出你能给的最佳推断。"
    )
    text, history = get_completion(prompt, (
        f"数据集概况：rows={p.rows}, cols={p.cols}\n"
        f"列名：\n{chr(10).join(schema_lines)}\n\n"
        f"样例行CSV：\n{sample_csv.getvalue()}\n\n"
        f"问题：{question}"
    ))
    if not text:
        return False, "AI 返回为空（已使用非 AI 洞察）。"
    return True, text


INDEX_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>AI 数据分析小面板（MVP）</title>
  <style>
    :root{
      --bg1:#0b1020; --bg2:#0f172a; --card:#0b1220cc;
      --text:#e5e7eb; --muted:#94a3b8; --border:#22304a;
      --accent:#60a5fa; --accent2:#a78bfa; --good:#34d399; --warn:#fbbf24;
    }
    body{
      font-family:ui-sans-serif,system-ui,Segoe UI,Arial;
      margin:0;
      background: radial-gradient(1200px 600px at 20% 0%, #1d4ed833, transparent 60%),
                  radial-gradient(900px 500px at 90% 10%, #7c3aed22, transparent 55%),
                  linear-gradient(180deg, var(--bg2), var(--bg1));
      color:var(--text);
    }
    .wrap{max-width:1100px;margin:0 auto;padding:28px 20px 48px}
    .title{display:flex;align-items:flex-end;justify-content:space-between;gap:16px}
    h2{margin:0;font-size:22px;letter-spacing:.2px}
    .muted{color:var(--muted);font-size:13px}
    .card{
      background:linear-gradient(180deg, #0b1220cc, #0b122099);
      border:1px solid var(--border);
      border-radius:16px;
      padding:16px;
      margin:14px 0;
      box-shadow:0 8px 30px rgba(0,0,0,.25);
      backdrop-filter: blur(6px);
    }
    .grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}
    input[type=text]{
      width:100%;padding:10px 12px;border:1px solid var(--border);
      border-radius:12px;background:#0b1220;color:var(--text);
      outline:none;
    }
    input[type=file]{width:100%;color:var(--muted)}
    button{
      padding:10px 14px;border-radius:12px;border:1px solid #2a3a58;
      background:linear-gradient(135deg, #111827, #0b1220);
      color:#fff;cursor:pointer;
    }
    button:hover{border-color:#3b82f6}
    code{background:#0b1220;border:1px solid var(--border);padding:2px 6px;border-radius:8px}
    pre{white-space:pre-wrap;background:#060a14;color:var(--text);padding:12px;border-radius:14px;overflow:auto;border:1px solid var(--border)}
    table{border-collapse:collapse;width:100%}
    th,td{border:1px solid var(--border);padding:7px 8px;text-align:left;font-size:13px}
    th{color:#cbd5e1;background:#0b1220}
    .kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}
    .kpi{padding:12px;border-radius:14px;border:1px solid var(--border);background:#0b1220}
    .kpi .v{font-size:18px;font-weight:700}
    .kpi .l{font-size:12px;color:var(--muted)}
    .pill{display:inline-block;padding:4px 10px;border-radius:999px;border:1px solid var(--border);color:var(--muted);font-size:12px}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="title">
      <div>
        <h2>AI 数据分析小面板（MVP → Dashboard）</h2>
        <div class="muted">上传 CSV → 自动洞察 + 可视化 → 可选 AI 问答（没 Key 也能用）</div>
      </div>
      <span class="pill">零依赖 · 本地运行</span>
    </div>
    <div class="card">
      <form action="/analyze" method="post" enctype="multipart/form-data">
        <div class="grid">
          <div>
            <label><b>上传 CSV</b></label><br/>
            <input name="file" type="file" accept=".csv" required />
          </div>
          <div>
            <label><b>可选：问一个问题</b></label><br/>
            <input name="q" type="text" placeholder="例如：提前几天买更划算？哪家航司更贵？" />
          </div>
        </div>
        <div style="margin-top:12px">
          <button type="submit">生成仪表盘</button>
        </div>
      </form>
    </div>
    <div class="card">
      <b>提示</b>
      <ul class="muted">
        <li>如需 AI：设置 <code>OPENAI_API_KEY</code>（可选 <code>OPENAI_MODEL</code>）。</li>
        <li>你现在这份数据是机票价格数据，面板会自动生成“价格驱动因素”相关图表。</li>
      </ul>
    </div>
  </div>
</body>
</html>
"""


def html_table(rows: list[dict[str, str]], headers: list[str], limit_cols: int = 12) -> str:
    hs = headers[:limit_cols]
    out = ["<table><thead><tr>"]
    out += [f"<th>{escape(h)}</th>" for h in hs]
    out.append("</tr></thead><tbody>")
    for r in rows:
        out.append("<tr>")
        out += [f"<td>{escape(str(r.get(h, '')))}</td>" for h in hs]
        out.append("</tr>")
    out.append("</tbody></table>")
    return "".join(out)


def json_for_chart(pairs: list[tuple[str, float]], limit: int = 10) -> tuple[list[str], list[float]]:
    xs = [k for k, _ in pairs[:limit]]
    ys = [float(v) for _, v in pairs[:limit]]
    return xs, ys


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: str, content_type: str = "text/html; charset=utf-8") -> None:
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/":
            self._send(200, INDEX_HTML)
            return
        self._send(404, "Not Found", "text/plain; charset=utf-8")

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/analyze":
            self._send(404, "Not Found", "text/plain; charset=utf-8")
            return

        try:
            import cgi

            form = cgi.FieldStorage(fp=self.rfile, headers=self.headers, environ={"REQUEST_METHOD": "POST"})
            file_item = form["file"] if "file" in form else None
            q_item = form["q"] if "q" in form else None
            question = (q_item.value if q_item is not None else "").strip()

            # cgi.FieldStorage 禁止 bool 判断（会抛 TypeError）
            if file_item is None or getattr(file_item, "file", None) is None or not getattr(file_item, "filename", ""):
                self._send(400, "Missing file. Please choose a CSV file.", "text/plain; charset=utf-8")
                return

            # best-effort decode as utf-8; fall back with replacement
            raw = file_item.file.read()
            try:
                text = raw.decode("utf-8")
            except Exception:
                text = raw.decode("utf-8", errors="replace")

            p = profile_csv(text)
            insight_lines = rule_insights(p)

            ai_block = ""
            if question:
                ok, ans = openai_answer(question, p)
                if ok:
                    ai_block = f"<div class='card'><b>AI 回答</b><pre>{escape(ans)}</pre></div>"
                else:
                    ai_block = f"<div class='card'><b>AI（降级/失败）</b><pre>{escape(ans)}</pre></div>"

            k = p.kpi

            def fmt_money(v: float) -> str:
                return f"{v:,.0f}"

            # charts
            airline_x, airline_y = json_for_chart(p.avg_price_by.get("airline", []), limit=8)
            class_x, class_y = json_for_chart(p.avg_price_by.get("class", []), limit=6)
            stops_x, stops_y = json_for_chart(p.avg_price_by.get("stops", []), limit=6)
            dep_x, dep_y = json_for_chart(p.avg_price_by.get("departure_time", []), limit=8)
            trend_x = [x for x, _ in p.trend_days_left]
            trend_y = [float(y) for _, y in p.trend_days_left]

            style_block = INDEX_HTML.split("<style>")[1].split("</style>")[0]
            body = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>分析结果</title>
<style>{style_block}</style>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
</head>
<body>
  <div class="wrap">
    <div class="title">
      <div>
        <h2>分析结果</h2>
        <div class="muted">从 CSV 生成的可视化仪表盘（本地运行）</div>
      </div>
      <div>
        <a class="pill" style="text-decoration:none" href="/">← 返回上传</a>
      </div>
    </div>

    <div class="card">
      <div class="kpis">
        <div class="kpi"><div class="v">{k.get("avg_price", 0.0) and fmt_money(k.get("avg_price", 0.0))}</div><div class="l">均价（price）</div></div>
        <div class="kpi"><div class="v">{k.get("p50_price", 0.0) and fmt_money(k.get("p50_price", 0.0))}</div><div class="l">中位数（P50）</div></div>
        <div class="kpi"><div class="v">{k.get("min_price", 0.0) and fmt_money(k.get("min_price", 0.0))}</div><div class="l">最低价</div></div>
        <div class="kpi"><div class="v">{k.get("max_price", 0.0) and fmt_money(k.get("max_price", 0.0))}</div><div class="l">最高价</div></div>
      </div>
      <div class="muted" style="margin-top:10px">行数 {p.rows} · 列数 {p.cols}</div>
    </div>

    <div class="grid">
      <div class="card">
        <b>价格驱动因素：航司（Top）</b>
        <div class="muted">按 airline 的均价（样本>=10 的类别）</div>
        <canvas id="c_airline" height="140"></canvas>
      </div>
      <div class="card">
        <b>价格驱动因素：舱位 / 中转</b>
        <div class="muted">按 class、stops 的均价</div>
        <canvas id="c_class" height="120"></canvas>
        <div style="height:10px"></div>
        <canvas id="c_stops" height="120"></canvas>
      </div>
    </div>

    <div class="grid">
      <div class="card">
        <b>购买时机：提前多久 vs 价格</b>
        <div class="muted">按 days_left 分桶后的均价（粗粒度趋势）</div>
        <canvas id="c_days" height="140"></canvas>
      </div>
      <div class="card">
        <b>时段影响：起飞时间</b>
        <div class="muted">按 departure_time 的均价</div>
        <canvas id="c_dep" height="140"></canvas>
      </div>
    </div>

    <div class="card">
      <b>自动洞察（可扫描版）</b>
      <pre>{escape(chr(10).join(insight_lines))}</pre>
    </div>

    <div class="card">
      <b>数据预览（最多 30 行，最多 12 列）</b><br/>
      {html_table(p.sample_rows, p.headers)}
    </div>

    {ai_block}
  </div>

  <script>
    const theme = {{
      grid: 'rgba(148,163,184,.18)',
      ticks: 'rgba(226,232,240,.9)',
      bg1: 'rgba(96,165,250,.85)',
      bg2: 'rgba(167,139,250,.85)',
      bg3: 'rgba(52,211,153,.85)',
    }};

    function barChart(id, labels, data, color) {{
      const el = document.getElementById(id);
      if (!el || !labels || labels.length===0) return;
      new Chart(el, {{
        type: 'bar',
        data: {{
          labels: labels,
          datasets: [{{ label: 'avg price', data: data, backgroundColor: color, borderRadius: 10 }}]
        }},
        options: {{
          responsive: true,
          plugins: {{ legend: {{ display:false }} }},
          scales: {{
            x: {{ ticks: {{ color: theme.ticks }}, grid: {{ color: 'rgba(0,0,0,0)' }} }},
            y: {{ ticks: {{ color: theme.ticks }}, grid: {{ color: theme.grid }} }}
          }}
        }}
      }});
    }}

    function lineChart(id, labels, data, color) {{
      const el = document.getElementById(id);
      if (!el || !labels || labels.length===0) return;
      new Chart(el, {{
        type: 'line',
        data: {{
          labels: labels,
          datasets: [{{ label: 'avg price', data: data, borderColor: color, backgroundColor: color, tension: .35, pointRadius: 3 }}]
        }},
        options: {{
          responsive: true,
          plugins: {{ legend: {{ display:false }} }},
          scales: {{
            x: {{ ticks: {{ color: theme.ticks }}, grid: {{ color: 'rgba(0,0,0,0)' }} }},
            y: {{ ticks: {{ color: theme.ticks }}, grid: {{ color: theme.grid }} }}
          }}
        }}
      }});
    }}

    barChart('c_airline', {json.dumps(airline_x)}, {json.dumps(airline_y)}, theme.bg1);
    barChart('c_class', {json.dumps(class_x)}, {json.dumps(class_y)}, theme.bg2);
    barChart('c_stops', {json.dumps(stops_x)}, {json.dumps(stops_y)}, theme.bg3);
    barChart('c_dep', {json.dumps(dep_x)}, {json.dumps(dep_y)}, 'rgba(251,191,36,.85)');
    lineChart('c_days', {json.dumps(trend_x)}, {json.dumps(trend_y)}, 'rgba(96,165,250,.95)');
  </script>
</body></html>"""
            self._send(200, body)
        except Exception:
            tb = traceback.format_exc()
            self._send(500, tb, "text/plain; charset=utf-8")


def main() -> None:
    # Load .env if present (so you don't have to export env vars every time)
    load_dotenv_simple(".env")
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8001"))
    httpd = HTTPServer((host, port), Handler)
    print(f"Serving on http://{host}:{port} (Ctrl+C to stop)")
    httpd.serve_forever()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped.")
        sys.exit(0)

