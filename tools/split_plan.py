#!/usr/bin/env python3
"""只讀估算：audio/ 依科目的大小，以及 GitHub Pages 每站 1GB 下怎麼拆 repo。

不修改、不移動、不刪除任何檔案，也不讀寫網路。數字只印到 stdout，可重跑。

上限預設 1,000,000,000 bytes：GitHub 文件寫「Published GitHub Pages sites
may be no larger than 1 GB」，沒有寫明是 10^9 還是 2^30。這裡用較小的十進位
1 GB，避免把餘裕估高。改用 1 GiB 時加上 --limit-bytes 1073741824。

容量公式（整數除法，無條件捨去，不四捨五入）：

    站點內容 = audio/ + episodes.json + 播放器殼
              （不含 .git、docs/、tools/）
    這一站還能再放的集數
        = (上限 - 站點內容) * 集數 // (audio 位元組 + episodes.json 位元組)
    一批再放幾批 = 上面的集數 // --batch-size

新 repo 只計該 repo 的 mp3 位元組（目錄與播放器留在主站）。
某一科檔案缺漏時，該科大小與餘裕印「待查」，不用其他科的平均值填。
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

LIMIT_DEFAULT = 1_000_000_000
BATCH_DEFAULT = 10
SUBJECTS_PER_REPO_DEFAULT = 3


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def fmt_bytes(n: int) -> str:
    return f"{n:,} bytes（{n / 1_000_000:.2f} MB）"


def fmt_dur(seconds: int) -> str:
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h} 小時 {m} 分 {s} 秒（{int(seconds):,} 秒）"


def median_low(values: list[int]) -> int | None:
    """奇數取正中；偶數取中間兩數的較小者，門檻維持整數。"""
    if not values:
        return None
    ys = sorted(values)
    n = len(ys)
    if n % 2 == 1:
        return ys[n // 2]
    return ys[n // 2 - 1]


def detect_pages(root: Path) -> tuple[str | None, str | None]:
    readme = root / "README.md"
    if not readme.is_file():
        return None, None
    text = readme.read_text(encoding="utf-8")
    match = re.search(
        r"https://([A-Za-z0-9-]+)\.github\.io/([A-Za-z0-9._-]+)/?",
        text,
    )
    if not match:
        return None, None
    return f"https://{match.group(1)}.github.io", match.group(2)


def categorize(rel: Path) -> str:
    if not rel.parts:
        return "shell"
    if rel.parts[0] == "audio":
        return "audio"
    if rel.as_posix() == "episodes.json":
        return "episodes"
    if rel.parts[0] == "docs":
        return "docs"
    if rel.parts[0] == "tools":
        return "tools"
    return "shell"


def scan_tree(root: Path) -> dict[str, list[tuple[str, int]]]:
    groups: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if name != ".git"]
        for name in filenames:
            path = Path(dirpath) / name
            if ".git" in path.parts:
                continue
            rel = path.relative_to(root)
            groups[categorize(rel)].append((rel.as_posix(), path.stat().st_size))
    return groups


def load_episodes(root: Path) -> tuple[dict, list[dict]]:
    path = root / "episodes.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("episodes"), list):
        raise ValueError("episodes.json 不是 {episodes: [...]} 物件")
    return data, data["episodes"]


def audio_name(audio: str) -> str | None:
    if not isinstance(audio, str) or not audio:
        return None
    path = audio.split("?", 1)[0]
    if path.startswith("audio/"):
        return path[len("audio/") :]
    return os.path.basename(path)


def prefix_of(name: str) -> str | None:
    stem = name.rsplit(".", 1)[0]
    if "-" not in stem:
        return None
    prefix, _, tail = stem.rpartition("-")
    if not prefix or not tail.isdigit():
        return None
    return prefix


class Subject:
    def __init__(self, name: str) -> None:
        self.name = name
        self.ids: list[str] = []
        self.dur = 0
        self.dur_ok = True
        self.bytes = 0
        self.bytes_ok = True
        self.prefixes: set[str] = set()

    @property
    def count(self) -> int:
        return len(self.ids)


def build_subjects(
    episodes: list[dict], audio_files: dict[str, int]
) -> tuple[list[Subject], list[str], set[str]]:
    """回傳（出現順序的科目、異常訊息、有被 episodes 用到的檔名）。"""
    issues: list[str] = []
    seen: dict[str, Subject] = {}
    order: list[Subject] = []
    used: set[str] = set()
    ids: set[str] = set()

    for index, ep in enumerate(episodes):
        if not isinstance(ep, dict):
            issues.append(f"episodes[{index}] 不是物件")
            continue
        subj_name = ep.get("subj")
        ep_id = ep.get("id")
        if not isinstance(subj_name, str) or not subj_name:
            issues.append(f"episodes[{index}] 沒有科目名稱")
            continue
        if not isinstance(ep_id, str) or not ep_id:
            issues.append(f"episodes[{index}]（{subj_name}）沒有 id")
            continue
        if ep_id in ids:
            issues.append(f"id 重複：{ep_id}")
        ids.add(ep_id)
        if subj_name not in seen:
            seen[subj_name] = Subject(subj_name)
            order.append(seen[subj_name])
        subject = seen[subj_name]
        subject.ids.append(ep_id)

        dur = ep.get("dur")
        if isinstance(dur, int) and not isinstance(dur, bool) and dur >= 0:
            subject.dur += dur
        else:
            subject.dur_ok = False
            issues.append(f"{ep_id} 的 dur 不是非負整數：{dur!r}")

        audio = ep.get("audio")
        name = audio_name(audio) if isinstance(audio, str) else None
        version = ep.get("v")
        if not isinstance(audio, str):
            subject.bytes_ok = False
            issues.append(f"{ep_id} 沒有 audio 字串")
        else:
            path_only, _, query = audio.partition("?")
            expected = f"audio/{ep_id}.mp3"
            if path_only != expected:
                issues.append(f"{ep_id} 的 audio 路徑不是 {expected}：{path_only}")
            if not isinstance(version, str) or query != f"v={version}":
                issues.append(
                    f"{ep_id} 的 audio 查詢參數不是 v={version!r}：{query or '（無）'}"
                )
        if name is None:
            subject.bytes_ok = False
        else:
            prefix = prefix_of(name)
            if prefix:
                subject.prefixes.add(prefix)
            else:
                issues.append(f"{ep_id} 的檔名無法取出科目前綴：{name}")
            if name in audio_files:
                subject.bytes += audio_files[name]
                used.add(name)
            else:
                subject.bytes_ok = False
                issues.append(f"{ep_id} 的音檔不在 audio/：{name}")

    prefix_owner: dict[str, set[str]] = defaultdict(set)
    for subject in order:
        if len(subject.prefixes) != 1:
            issues.append(
                f"{subject.name} 的檔名前綴不是恰好一個：{sorted(subject.prefixes) or '（無）'}"
            )
        for prefix in subject.prefixes:
            prefix_owner[prefix].add(subject.name)
    for prefix, owners in sorted(prefix_owner.items()):
        if len(owners) > 1:
            issues.append(f"前綴 {prefix} 對到多個科目：{sorted(owners)}")

    return order, issues, used


def top_level_url_fields(episodes: list[dict]) -> dict[str, int]:
    """頂層字串裡出現音檔或 http 的欄位。逐字稿內文不算網址欄位。"""
    counts: dict[str, int] = defaultdict(int)
    for ep in episodes:
        if not isinstance(ep, dict):
            continue
        for key, value in ep.items():
            if not isinstance(value, str):
                continue
            if (
                "audio/" in value
                or ".mp3" in value
                or value.startswith("http://")
                or value.startswith("https://")
                or value.startswith("/")
            ):
                counts[key] += 1
    return dict(counts)


def orphans(audio_files: dict[str, int], used: set[str]) -> list[tuple[str, int]]:
    return sorted(
        (name, audio_files[name]) for name in audio_files if name not in used
    )


def more_episodes(remaining: int, total_bytes: int, count: int) -> int | None:
    if remaining < 0 or total_bytes <= 0 or count <= 0:
        return None
    return (remaining * count) // total_bytes


def pack_repos(subjects: list[Subject], per_repo: int) -> list[list[Subject]]:
    """音檔由大到小，輪流分到 ceil(科數 / per_repo) 個 repo。"""
    ranked = sorted(
        subjects,
        key=lambda subject: (
            -(subject.bytes if subject.bytes_ok else -1),
            subject.name,
        ),
    )
    if not ranked:
        return []
    repo_count = max(1, math.ceil(len(ranked) / per_repo))
    buckets: list[list[Subject]] = [[] for _ in range(repo_count)]
    for index, subject in enumerate(ranked):
        buckets[index % repo_count].append(subject)
    return buckets


def repo_slug(base: str, subjects: list[Subject]) -> str:
    if not base:
        return "待查"
    prefixes: list[str] = []
    for subject in subjects:
        if len(subject.prefixes) == 1:
            prefixes.append(next(iter(subject.prefixes)))
        else:
            prefixes.append("待查")
    return base + "-" + "-".join(prefixes)


def print_subject_table(subjects: list[Subject], limit: int) -> None:
    print("| 科目 | 前綴 | 集數 | 時長 | 音檔大小 | 占上限 | 標記 |")
    print("| --- | --- | ---: | --- | ---: | ---: | --- |")
    counts = [subject.count for subject in subjects]
    mid = median_low(counts)
    threshold = None if mid is None else mid // 2
    for subject in subjects:
        prefix = (
            next(iter(subject.prefixes)) if len(subject.prefixes) == 1 else "待查"
        )
        dur = fmt_dur(subject.dur) if subject.dur_ok else "待查"
        if subject.bytes_ok:
            size = f"{subject.bytes:,}"
            share = f"{subject.bytes / limit * 100:.2f}%"
        else:
            size = "待查"
            share = "待查"
        mark = ""
        if threshold is not None and subject.count <= threshold:
            mark = "明顯偏少" if subject.count > 0 else "集數為 0"
        print(
            f"| {subject.name} | {prefix} | {subject.count} | {dur} | {size} | {share} | {mark} |"
        )


def print_repo_table(
    buckets: list[list[Subject]],
    *,
    base: str,
    origin: str | None,
    limit: int,
    batch_size: int,
) -> None:
    print("| 新 repo | 科目 | 集數 | 預估大小（bytes） | 還能再放集數 | 每批還能再放 | 網址前綴 |")
    print("| --- | --- | ---: | ---: | ---: | ---: | --- |")
    for bucket in buckets:
        names = "、".join(subject.name for subject in bucket)
        count = sum(subject.count for subject in bucket)
        ok = all(subject.bytes_ok for subject in bucket) and count > 0
        slug = repo_slug(base, bucket) if base else "待查"
        if origin and base:
            prefix = f"{origin}/{slug}/audio/"
        else:
            prefix = "待查"
        if not ok:
            print(f"| {slug} | {names} | {count} | 待查 | 待查 | 待查 | {prefix} |")
            continue
        total = sum(subject.bytes for subject in bucket)
        remaining = limit - total
        extra = more_episodes(remaining, total, count)
        batches = None if extra is None else extra // batch_size
        extra_text = "待查" if extra is None else f"{extra:,}"
        batch_text = "待查" if batches is None else f"{batches:,}"
        print(
            f"| {slug} | {names} | {count} | {total:,} | {extra_text} | {batch_text} | {prefix} |"
        )


def print_compare(more: int | None, batches: list[int]) -> None:
    if more is None:
        print("這一站還能再放的集數：待查。歷史批大小無法換算。")
        return
    if not batches:
        print("未提供 --compare-batches。歷史各批能再放幾批：待查。")
        return
    print("下列批大小來自參數，腳本不讀 git。")
    for size in batches:
        if size <= 0:
            print(f"- 批大小 {size}：待查")
            continue
        print(f"- 一批 {size} 集：還能再放 {more // size} 批（{more // size * size} 集），餘 {more % size} 集不夠再湊一批")
    average_num = more * len(batches)
    average_den = sum(batches)
    if average_den <= 0:
        print("- 這些批的平均：待查")
        return
    fitted = average_num // average_den
    print(
        f"- 以這 {len(batches)} 個批大小的平均（合計 {average_den} 集 / {len(batches)} 批）換算："
        f"還能再放 {fitted} 批"
    )


def parse_batches(text: str | None) -> list[int]:
    if not text:
        return []
    values: list[int] = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        values.append(int(part))
    return values


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="只讀統計 audio/ 依科目的大小，並估算 GitHub Pages 1GB 上限下的拆分。"
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="倉庫根目錄，預設是本腳本的上一層",
    )
    parser.add_argument(
        "--limit-bytes",
        type=int,
        default=LIMIT_DEFAULT,
        help="每站上限，預設 1000000000（十進位 1GB）",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=BATCH_DEFAULT,
        help="「一批」的集數，預設 10（對齊 commit 裡的「各 10 集」）",
    )
    parser.add_argument(
        "--subjects-per-repo",
        type=int,
        default=SUBJECTS_PER_REPO_DEFAULT,
        help="方案 B 每個 repo 放幾科，預設 3",
    )
    parser.add_argument(
        "--compare-batches",
        default="",
        help="逗號分隔的歷史批集數，例如 30,24,20,33,40。不讀 git。",
    )
    parser.add_argument(
        "--pages-origin",
        default="",
        help="覆寫頁面網域，例如 https://italymanoec.github.io。預設從 README 的公開網址讀。",
    )
    parser.add_argument(
        "--repo-base",
        default="",
        help="新 repo 名稱前綴，預設是 README 公開網址的專案路徑（law-podcast）",
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    if args.limit_bytes <= 0:
        print("上限必須是正整數", file=sys.stderr)
        return 2
    if args.batch_size <= 0 or args.subjects_per_repo <= 0:
        print("--batch-size 與 --subjects-per-repo 必須是正整數", file=sys.stderr)
        return 2

    root = (args.root or repo_root()).resolve()
    try:
        data, episodes = load_episodes(root)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"讀不到 episodes.json（{exc}）。集數、時長、拆分估算：待查。", file=sys.stderr)
        return 2

    tree = scan_tree(root)
    audio_files = {
        os.path.basename(path): size for path, size in tree.get("audio", [])
    }
    # 同名檔（不該出現）會互相覆蓋；先擋下來。
    audio_names = [os.path.basename(path) for path, _size in tree.get("audio", [])]
    if len(audio_names) != len(set(audio_names)):
        print("audio/ 有同名檔，大小：待查。", file=sys.stderr)
        return 2

    subjects, issues, used = build_subjects(episodes, audio_files)
    url_fields = top_level_url_fields(episodes)
    extra_url_fields = sorted(key for key in url_fields if key != "audio")
    if extra_url_fields:
        issues.append(
            "頂層還有其他像網址的欄位："
            + "、".join(f"{key}（{url_fields[key]} 筆）" for key in extra_url_fields)
        )
    unused = orphans(audio_files, used)
    for name, size in unused:
        issues.append(f"audio/ 有未被 episodes.json 引用的檔：{name}（{size:,} bytes）")

    origin, repo_base = detect_pages(root)
    if args.pages_origin:
        origin = args.pages_origin.rstrip("/")
    if args.repo_base:
        repo_base = args.repo_base
    if origin is None or repo_base is None:
        print("README 沒有 https://<帳號>.github.io/<repo>/ 公開網址。跨 repo 網址：待查。")

    audio_total = sum(size for _path, size in tree.get("audio", []))
    json_total = sum(size for _path, size in tree.get("episodes", []))
    shell_files = tree.get("shell", [])
    shell_total = sum(size for _path, size in shell_files)
    docs_total = sum(size for _path, size in tree.get("docs", []))
    tools_total = sum(size for _path, size in tree.get("tools", []))
    site_core = audio_total + json_total + shell_total
    published = site_core + docs_total + tools_total

    counted = sum(subject.bytes for subject in subjects if subject.bytes_ok)
    missing_bytes = any(not subject.bytes_ok for subject in subjects) or bool(unused)
    dur_ok = all(subject.dur_ok for subject in subjects) and bool(subjects)
    total_dur = sum(subject.dur for subject in subjects)

    print("# 拆分估算")
    print()
    print(f"- 倉庫：{root}")
    print(f"- episodes.json built：{data.get('built')!r}")
    print(f"- 集數：{len(episodes)}")
    print(f"- 上限：{fmt_bytes(args.limit_bytes)}")
    print(f"- 預設一批：{args.batch_size} 集")
    print(f"- MB / GB：十進位（1 MB = 1,000,000 bytes，1 GB = 1,000,000,000 bytes）")
    print()
    print("## 檔案合計")
    print()
    print(f"- audio/：{fmt_bytes(audio_total)}，{len(audio_files)} 個檔")
    print(f"- episodes.json：{fmt_bytes(json_total)}")
    print(f"- 播放器殼：{fmt_bytes(shell_total)}")
    for path, size in sorted(shell_files):
        print(f"  - {path}：{size:,} bytes")
    print(f"- 站點內容（不含 .git、docs/、tools/）：{fmt_bytes(site_core)}")
    print(f"- docs/：{fmt_bytes(docs_total)}")
    print(f"- tools/：{fmt_bytes(tools_total)}")
    print(f"- 除 .git 外全部：{fmt_bytes(published)}")
    if audio_files:
        largest = max(audio_files.items(), key=lambda item: item[1])
        print(f"- 最大單一音檔：{largest[0]} {largest[1]:,} bytes")
    else:
        print("- 最大單一音檔：待查")
    print()
    print("## 各科（集數、時長、音檔）")
    print()
    if not subjects:
        print("沒有科目。各科集數：待查。")
    else:
        print_subject_table(subjects, args.limit_bytes)
    print()
    if dur_ok:
        print(f"- 總集數：{len(episodes)}")
        print(f"- 總時長：{fmt_dur(total_dur)}（各集 dur 加總；沒有解析 mp3）")
    else:
        print("- 總時長：待查")
    print("- 音檔實際長度是否等於 dur：待查（本腳本不解析 mp3）")
    counts = [subject.count for subject in subjects]
    mid = median_low(counts)
    if mid is None:
        print("- 明顯偏少門檻：待查")
    else:
        threshold = mid // 2
        print(
            f"- 明顯偏少門檻：已出現科目的集數中位數（較低中位數）為 {mid}，"
            f"集數 ≤ {threshold} 標成明顯偏少；0 集另標。"
        )
        low = [
            subject
            for subject in subjects
            if subject.count <= threshold
        ]
        if low:
            for subject in low:
                label = "集數為 0" if subject.count == 0 else "明顯偏少"
                print(f"  - {subject.name}：{subject.count} 集（{label}）")
        else:
            print("  - 已出現的科目裡沒有落在門檻以內的。")
        below = [
            subject
            for subject in subjects
            if threshold < subject.count < mid
        ]
        if below:
            listed = "、".join(f"{subject.name} {subject.count} 集" for subject in below)
            print(f"- 低於中位數、但高於門檻，不標成明顯偏少：{listed}")
    print("- 集數為 0 的科目：待查。episodes.json 只有已收錄的集，沒有「應收錄科目」清單，不臆造科目名稱。")
    if missing_bytes:
        print(
            f"- 已對到音檔的位元組 {counted:,}，與 audio/ 合計 {audio_total:,} 不一致或有缺檔。"
            "缺檔科目的大小維持待查。"
        )
    elif counted != audio_total:
        print(
            f"- 科目音檔加總 {counted:,} 與 audio/ 合計 {audio_total:,} 不一致：大小待查。"
        )
    else:
        print(f"- 科目音檔加總等於 audio/ 合計：{audio_total:,} bytes")
    print()

    print("## 目前這一站還能再放多少")
    print()
    print("餘裕先扣掉播放器殼與 episodes.json，再以「音檔 + episodes.json」的平均每集增量來除。")
    print("docs/ 與 tools/ 是規劃檔。下面兩個集數都印；批數以「把規劃檔也算進發布」的那個為準，比較保守。")
    core_remaining = args.limit_bytes - site_core
    full_remaining = args.limit_bytes - published
    grow = audio_total + json_total
    core_more = more_episodes(core_remaining, grow, len(episodes))
    full_more = more_episodes(full_remaining, grow, len(episodes))
    print(f"- 不含規劃檔的餘裕：{fmt_bytes(core_remaining) if core_remaining >= 0 else '已超過上限'}")
    print(f"- 含規劃檔的餘裕：{fmt_bytes(full_remaining) if full_remaining >= 0 else '已超過上限'}")
    if core_more is None:
        print("- 不含規劃檔還能再放：待查")
    else:
        print(
            f"- 不含規劃檔還能再放：{core_more:,} 集，"
            f"以每批 {args.batch_size} 集計為 {core_more // args.batch_size:,} 批"
            f"（剩下 {core_more % args.batch_size} 集不夠一批）"
        )
    if full_more is None:
        print("- 含規劃檔還能再放：待查")
    else:
        print(
            f"- 含規劃檔還能再放：{full_more:,} 集，"
            f"以每批 {args.batch_size} 集計為 {full_more // args.batch_size:,} 批"
            f"（剩下 {full_more % args.batch_size} 集不夠一批）"
        )
    try:
        compare = parse_batches(args.compare_batches)
    except ValueError:
        print("--compare-batches 不是整數清單", file=sys.stderr)
        return 2
    print_compare(full_more, compare)
    print()
    print("搬走音檔之後，主站發布檔大約只剩 episodes.json 與播放器殼。")
    main_bytes = json_total + shell_total
    main_remaining = args.limit_bytes - main_bytes
    main_more = more_episodes(main_remaining, json_total, len(episodes))
    print(f"- 主站若不再放 mp3：約 {fmt_bytes(main_bytes)}")
    if main_more is None:
        print("- 主站以目前 episodes.json 的平均增量，還能再收錄的目錄筆數：待查")
    else:
        print(
            f"- 主站以目前每集目錄增量，大約還能再收 {main_more:,} 筆才碰到這個上限。"
            "這不是音檔容量；音檔要看下面各 repo。"
        )
    print()

    print("## 方案 A：每科一個 repo")
    print()
    print("建議用這個。最終每科要收到幾集並沒有寫在倉庫裡（待查）；")
    print("跟別科共用 repo 時，長得快的那一科會把同站其他科一起頂到上限，還得再拆。")
    print("目前每個前綴只對一個科目，repo 後綴就用檔名前綴，不必另建對照表。")
    if origin is None or repo_base is None:
        print("網址前綴：待查（README 沒有公開網址，或格式不符）。")
    else:
        print(f"帳號網域：{origin}（與主站同一個 github.io，瀏覽器視為同一個 origin）。")
        print(f"repo 名稱：{repo_base}-<前綴>。這些 repo 都還沒有建立。")
    print()
    base = repo_base or ""
    print_repo_table(
        [[subject] for subject in subjects],
        base=base,
        origin=origin,
        limit=args.limit_bytes,
        batch_size=args.batch_size,
    )
    print()
    print("每個新 repo 要改的都是主站 `episodes[].audio` 裡、該科那些集的前綴。")
    print("檔名與 `?v=` 沿用原值。`v` 欄位維持原值，已下載的離線檔才對得上。")
    print()

    print(f"## 方案 B：每 {args.subjects_per_repo} 科一個 repo")
    print()
    print("科目依音檔位元組由大到小排序，輪流分進各 repo，避免大科全擠在同一站。")
    print("科目數不能整除時，排序較前的 repo 會多一科。重跑的分組相同。")
    print("餘裕用該 repo 目前各集的平均大小來除。若之後只加其中一科、而該科比平均大，實際會少於表上的批數；各科自己的數字見方案 A。")
    print()
    buckets = pack_repos(subjects, args.subjects_per_repo)
    print_repo_table(
        buckets,
        base=base,
        origin=origin,
        limit=args.limit_bytes,
        batch_size=args.batch_size,
    )
    print()

    print("## 要改的網址欄位")
    print()
    print("- 要改的只有 `episodes[].audio`。目前每一筆都是 `audio/<id>.mp3?v=<v>`。")
    print("  `index.html` 用這個字串當 `fetch(ep.audio)` 與 `<audio src>`。")
    if url_fields == {"audio": len(episodes)}:
        print(
            f"  各集頂層字串欄位裡，像網址的只有 `audio`（{url_fields['audio']} 筆）。"
        )
    elif not url_fields:
        print("  各集頂層沒有掃到像網址的字串。網址欄位：待查。")
    else:
        listed = "、".join(f"{key} {count} 筆" for key, count in sorted(url_fields.items()))
        print(f"  各集頂層像網址的欄位：{listed}。")
    print("- 維持原值：`id`、`v`、`subj`、`no`、`title`、`summary`、`dur`、`lines`、`marks`、`articles`。")
    print("  `id` 是離線儲存的鍵，`v` 是離線檔版本。網址換站而 `v` 不變時，已下載的檔仍對得上。")
    print("- `sw.js` 不讀 episodes.json。它的控管範圍是主站路徑，路徑含 `/audio/` 就略過。")
    print("  別的 repo 上的音檔在這個範圍之外。")
    print("- 相對路徑 `audio/...` 會繼續解析到主站底下。跨 repo 要寫絕對 https 網址。")
    if origin and repo_base and subjects:
        sample = subjects[0]
        sample_id = sample.ids[0]
        prefix = next(iter(sample.prefixes)) if len(sample.prefixes) == 1 else None
        if prefix:
            print(
                f"- 例：`audio/{sample_id}.mp3?v=<原 v>` → "
                f"`{origin}/{repo_base}-{prefix}/audio/{sample_id}.mp3?v=<原 v>`"
            )
        else:
            print("- 範例網址：待查（第一個科目的前綴不是恰好一個）。")
    else:
        print("- 範例網址：待查。")
    print("- 新 repo 若放在別的帳號，或主站改掛自訂網域，`fetch` 下載會變成跨網域。")
    print("  倉庫裡沒有自訂網域設定，實際是否已掛：待查。建議新 repo 留在 README 那個 github.io 帳號下。")
    print()

    print("## 異常")
    print()
    if issues:
        for issue in issues:
            print(f"- {issue}")
        print()
        return 1
    print("- 無。集數、檔名、前綴、`v` 與 audio/ 檔案一致，沒有缺檔或多餘檔。")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
