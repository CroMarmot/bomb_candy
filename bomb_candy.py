# uv init
# 需要ffmpeg完成音视频合并 apt install ffmpeg
# 注意 .python-version 和系统一致且有 python-dev包
# uv add "manim>=0.18.0,<1.0.0" "edge-tts>=6.1.14"
# uv run bomb_candy.py
"""
炸弹糖数学期望讲解视频生成器
- 动画：Manim Community (v0.18+)
- 语音：Edge TTS（中文女声）
- 自动合成音视频（需 ffmpeg）
"""

import asyncio
import shutil
import subprocess
import sys
from pathlib import Path

# Manim 配置（禁用音频，因为我们自己合成）
from manim import (
    config,
    Text,
    VGroup,
    MathTex,
    LEFT,
    DOWN,
    FadeIn,
    Scene,
    RIGHT,
    GREEN,
    WHITE,
    YELLOW,
    BLUE,
    RED,
    LaggedStart,
    UL,
    Axes,
    PURPLE,
    UP,
    FadeOut,
    Create,
    Line,
    Table,
    Write,
)

config.disable_caching = False
config.save_last_frame = False
config.renderer = "cairo"  # 必须用 cairo 渲染视频帧
config.media_dir = "temp/manim_media"  # Manim 内部缓存（不影响我们）


try:
    import edge_tts
except ImportError:
    raise ImportError("请安装 edge-tts: uv add 'edge-tts>=6.1.14'")

# 项目内路径
BASE_DIR = Path(__file__).parent.resolve()
TEMP_DIR = BASE_DIR / "temp"
OUTPUT_DIR = BASE_DIR / "output"

# 确保目录存在
TEMP_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

# 清空 temp（可选）
for f in TEMP_DIR.iterdir():
    if f.is_file():
        f.unlink()

# 各 section 的语音文本
SCRIPT_SEGMENTS = {
    "intro": "大家好！今天我们来讲解抖音流行的炸弹糖背后的数学期望问题。",
    "rules": (
        "规则是这样的：共有若干个糖果，其中1个是炸弹。"
        "抽到炸弹，本轮收益清零。任意时刻可以停止。"
    ),
    "recurrence": (
        "用动态规划建模：设 f v t 表示当前已获得价值 v、剩余 t 个糖果时的最大期望收益。"
        "此时有两个选择：一是停止，v；二是再抽i个。"
        "最优策略是在所有 i 中选择期望最大的那个。"
    ),
    "table": "常见的总量是30个。这里可以简化，考虑当前持有v与最大期望的关系。容易发现一半以前继续抽取都有助于期望。",
    "graph": "从图像可以看出，期望收益与当前已经拥有的关系。",
    "conclusion": "结论是：最大期望是拿一半。",
    "outro": "感谢您的观看，我们下次再见。",
}


async def generate_audio_segment(key: str) -> Path:
    """为某个 section 生成语音，返回 MP3 路径"""
    text = SCRIPT_SEGMENTS[key]
    mp3_path = TEMP_DIR / f"{key}.mp3"
    if not mp3_path.exists():
        print(f"🎙️ 生成语音: {key}")
        communicate = edge_tts.Communicate(
            text=text, voice="zh-CN-XiaoxiaoNeural", rate="+0%", volume="+0%"
        )
        await communicate.save(str(mp3_path))
    return mp3_path


def get_duration(file_path: Path, media_type: str = "audio") -> float:
    """用 ffprobe 获取音视频时长（秒）"""
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        f"format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(file_path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return float(result.stdout.strip())
    except Exception as e:
        print(f"⚠️ 无法获取 {media_type} 时长 {file_path}: {e}")
        return 0.0


def merge_video_audio_with_ffmpeg(
    video_in: Path, audio_in: Path, video_out: Path
) -> bool:
    video_codec: str = "libx264"
    audio_codec: str = "aac"

    v_duration = get_duration(video_in)
    a_duration = get_duration(audio_in)

    if v_duration is None or a_duration is None:
        print("無法取得時長，無法繼續合併")
        return False

    print(f"視頻時長: {v_duration:.2f} 秒")
    print(f"音頻時長: {a_duration:.2f} 秒")

    # 決定使用哪種策略
    if a_duration > v_duration:
        # 音頻明顯較長 → 循環視頻 + -shortest（盡量 copy，不重編碼）
        print("音頻較長 → 使用視頻循環 + 以音頻為準")
        cmd = [
            "ffmpeg",
            "-y",  # 覆盖输出文件
            "-i",
            str(video_in),  # 無限循環視頻
            "-i",
            str(audio_in),
            "-filter_complex",
            (
                "[0:v]split[v1][v2];"
                "[v2]trim=start_frame=999999,setpts=N/FRAME_RATE/TB[blank];"  # 获取最后一帧（安全方式）
                "[v1][blank]concat=n=2:v=1:a=0[vid];"
                "[vid]tpad=stop_mode=clone:stop_duration=1000000[finalv]"  # 克隆最后一帧无限延展
            ),
            "-map",
            "[finalv]",
            "-map",
            "1:a",
            "-shortest",  # 实际上这里不用 -shortest，因为我们要让视频延长；但通过音频控制时长
            "-c:v",
            video_codec,
            "-c:a",
            audio_codec,
            "-vsync",
            "vfr",  # 可选：避免时间戳问题
            str(video_out),
        ]
    else:
        # 視頻較長 → 需要 tpad 凍結畫面 + apad 補靜音（必須重編碼）
        print("視頻較長 → 使用凍結最後一幀 + 補靜音")
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(video_in),
            "-i",
            str(audio_in),
            "-af",
            "apad",  # 補靜音到視頻結束
            "-shortest",  # 实际上这里不用 -shortest，因为我们要让视频延长；但通过音频控制时长
            "-c:v",
            video_codec,
            "-c:a",
            audio_codec,
            # 不加 -shortest，讓最長流（視頻）決定長度
            str(video_out),
        ]

    try:
        # 這裡選擇不 capture_output，讓你看到進度條
        # 如果想靜默執行，可改成 capture_output=True, text=True
        subprocess.run(
            cmd,
            check=True,
            stdout=subprocess.DEVNULL,  # , stderr=subprocess.DEVNULL
        )
        print(f"合併完成：{video_out}")
        return True

    except subprocess.CalledProcessError as e:
        print("FFmpeg 執行失敗！")
        if e.stderr:
            print(e.stderr.decode())
        return False


def concatenate_videos(video_paths, output_path: Path):
    """用 ffmpeg 拼接多个视频"""
    list_file = TEMP_DIR / "concat_list.txt"
    with open(list_file, "w") as f:
        for vp in video_paths:
            f.write(f"file '{vp.resolve()}'\n")

    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(list_file),
        "-c",
        "copy",
        str(output_path),
    ]
    subprocess.run(
        cmd,
        check=True,
        stdout=subprocess.DEVNULL,  # , stderr=subprocess.DEVNULL
    )


# --- Scene 定义（只渲染无声视频） ---
class BombCandyIntro(Scene):
    def construct(self):
        title = Text("炸弹糖的数学期望", font_size=48)
        subtitle = Text("——何时该停止？", font_size=36).next_to(title, DOWN)
        self.play(Write(title), run_time=1.5)
        self.play(FadeIn(subtitle))
        self.wait(1)


class BombCandyRules(Scene):
    def construct(self):
        prob_text = (
            VGroup(
                Text("规则：", color=YELLOW),
                Text("• 共 b 个糖果，其中 1 个是炸弹", t2c={"炸弹": RED}),
                Text("• 每拿一个安全糖果，+1 价值"),
                Text("• 若抽到炸弹，本轮收益归零"),
                Text("• 可在任意时刻停止，锁定当前收益"),
                Text("• 目标：最大化期望收益"),
            )
            .arrange(DOWN, aligned_edge=LEFT)
            .scale(0.7)
            .to_edge(UP)
        )
        self.play(FadeIn(prob_text))
        self.wait(3)


class BombCandyRecurrence(Scene):
    def construct(self):
        title = Text("动态规划+期望", color=BLUE).to_edge(UP).scale(1.2)
        self.play(Write(title))
        self.wait(0.5)

        # === 第一部分：问题建模（全部 .scale(0.7)）===
        part1 = (
            VGroup(
                VGroup(
                    Text("状态 "), MathTex(r"f(v,\ t) ="), Text("之后的最大收益")
                ).arrange(RIGHT),
                VGroup(MathTex(r"v ="), Text(" 当前已获价值")).arrange(RIGHT),
                VGroup(MathTex(r"t ="), Text(" 剩余糖果数（含1个炸弹）")).arrange(
                    RIGHT
                ),
                VGroup(
                    Text("决策：选择抽取数量"),
                    MathTex(r"i \in \lbrace 0, 1, \cdots, t\rbrace"),
                ).arrange(RIGHT),
                VGroup(
                    Text("• 若 i = 0（停止）：收益 = "), MathTex(r"v", color=GREEN)
                ).arrange(RIGHT),
                VGroup(
                    Text("• 若 i ≥ 1（抽取 i 个）：安全概率 = "),
                    MathTex(
                        r"\frac{\binom{t-1}{i}}{\binom{t}{i}} = \frac{t - i}{t}",
                        color=GREEN,
                    ),
                ).arrange(RIGHT),
                VGroup(
                    Text("  期望收益 = "),
                    MathTex(r"\frac{t - i}{t} \cdot f(v + i,\ t - i)", color=GREEN),
                ).arrange(
                    RIGHT,
                ),
            )
            .arrange(DOWN, aligned_edge=LEFT)
            .scale(0.7)
            .next_to(title, DOWN)
            .to_edge(LEFT)
        )

        self.play(FadeIn(part1))
        self.wait(10)

        # 淡出第一部分
        self.play(FadeOut(part1))

        # === 第二部分：最优策略公式（也 .scale(0.7)）===
        optimal_text = Text("最优策略：")

        formula_line1 = MathTex(r"f(v,\ t) = \max_{0 \leq i \leq t}")
        formula_line2 = MathTex(
            r"""
            \begin{cases}
                v, & \text{if } i = 0 \\
                \dfrac{t - i}{t} \cdot f(v + i,\, t - i), & \text{if } 1 \leq i \leq t
            \end{cases}
            """
        )
        formula = VGroup(formula_line1, formula_line2).arrange(DOWN, aligned_edge=LEFT)

        part2 = (
            VGroup(optimal_text, formula)
            .arrange(DOWN, aligned_edge=LEFT)
            .next_to(title, DOWN)
            .to_edge(LEFT)
        )

        self.play(FadeIn(part2))
        self.wait(3)


class BombCandyTable(Scene):
    def construct(self):
        TOTAL = 30
        max_v = TOTAL - 1  # v ∈ [0, 29]

        # h[v] = f(v, 30 - v)
        h = [0.0] * (max_v + 1)

        # Base case: v = 29 → 剩余 1 个（炸弹），只能停止
        h[29] = 29.0

        # DP backwards from v = 28 down to 0
        for v in range(28, -1, -1):
            t = TOTAL - v  # remaining candies (≥2)
            best = float(v)  # i = 0: stop

            # Try all i from 1 to t
            for i in range(1, t + 1):
                new_v = v + i
                if new_v > max_v:
                    # Cannot exceed v=29 (since t - i would be <1, impossible to survive)
                    continue
                prob_safe = (t - i) / t
                expected = prob_safe * h[new_v]
                if expected > best:
                    best = expected
            h[v] = best

        def make_subtable(start, end):
            data = [["v", "h(v)"]]
            for v in range(start, end + 1):
                data.append([str(v), f"{h[v]:.3f}"])

            table = Table(data, include_outer_lines=True)
            entries = table.get_entries()
            cols = len(data[0])  # 应该是 2

            # 遍历数据行（跳过 header，即从 row=1 开始）
            for row in range(1, len(data)):
                v = start + (row - 1)
                if v <= 10:
                    for col in range(cols):
                        index = row * cols + col
                        entries[index].set_color(GREEN)
                elif v <= 15:
                    for col in range(cols):
                        index = row * cols + col
                        entries[index].set_color(YELLOW)

            return table

        # Create three tables
        table1 = make_subtable(0, 9)
        table2 = make_subtable(10, 19)
        table3 = make_subtable(20, 29)

        title = Text("期望总收益表（总量=30）", color=GREEN).to_edge(UP)
        self.play(Write(title))

        exp = (
            VGroup(
                MathTex(r"h(v) = f(v,30-v)"),
            )
            .arrange(
                RIGHT,
            )
            .scale(0.7)
            .next_to(title, DOWN)
        )
        self.play(Write(exp))
        self.wait(0.5)

        # Scale first, then arrange
        tables = (
            VGroup(table1, table2, table3)
            .scale(0.3)
            .arrange(RIGHT)
            .next_to(exp, direction=DOWN)
        )

        self.play(Create(tables))
        self.wait(4)


class BombCandyConclusion(Scene):
    def construct(self):
        # 居中的标题
        title = Text("结论(不论总数多少)", color=YELLOW, font_size=48).to_edge(UP)

        # 内容列表（左对齐，位于标题下方）
        bullet1 = Text("• 拿总数一半的获得最佳期望")
        bullet2 = Text("• 不论是一次性拿一半，还是一次拿一个")
        bullet2 = Text("    • 如果总量30个，那么就拿15个")

        # 长句子：使用 line_width 自动换行（推荐），或手动加 \n
        extended = Text(
            "延伸: 如果庄家加码单个价值，\n"
            "那么保持 '总持有' 与 '场上剩余' 的价值相等是最佳策略。",
            color=GREEN,
        ).scale(0.7)

        extended_example_1 = Text(
            "例如拿了15个，剩余15个，决定不玩了，但是庄家让场上变3倍价值，\n"
            "那么再拿5个：15 + 5x3 = (15-5)x3\n",
            color=WHITE,
        ).scale(0.6)

        extended_example_2 = Text(
            "例如拿了15个，剩余15个，决定不玩了，但是庄家让场上变5倍价值，\n"
            "那么再拿6个：15 + 6x5 = (15-6)x5\n",
            color=WHITE,
        ).scale(0.6)

        content = VGroup(
            bullet1, bullet2, extended, extended_example_1, extended_example_2
        ).arrange(DOWN, aligned_edge=LEFT)
        content.next_to(title, DOWN).to_edge(LEFT)

        self.play(Write(title))
        self.play(FadeIn(content, shift=DOWN, lag_ratio=0.3))
        self.wait(4)


class BombCandyGraph(Scene):
    def construct(self):
        TOTAL = 30
        max_v = TOTAL - 1
        h = [0.0] * (max_v + 1)
        h[29] = 29.0
        for v in range(28, -1, -1):
            t = TOTAL - v
            best = float(v)
            for i in range(1, t + 1):
                new_v = v + i
                if new_v > max_v:
                    continue
                prob_safe = (t - i) / t
                expected = prob_safe * h[new_v]
                if expected > best:
                    best = expected
            h[v] = best

        x_vals = list(range(0, 30))
        y_stop = x_vals
        y_optimal = h

        axes = Axes(
            x_range=[0, 29, 5],
            y_range=[0, max(h) + 1, 5],
            axis_config={"include_numbers": True},
            x_length=9,
            y_length=5.5,
        ).center()

        labels = axes.get_axis_labels(x_label="v", y_label="Expected Reward")

        graph_stop = axes.plot_line_graph(
            x_values=x_vals,
            y_values=y_stop,
            line_color=RED,
            vertex_dot_style=dict(stroke_width=2, fill_color=RED),
            vertex_dot_radius=0.04,
        )

        graph_optimal = axes.plot_line_graph(
            x_values=x_vals,
            y_values=y_optimal,
            line_color=YELLOW,
            vertex_dot_style=dict(stroke_width=2, fill_color=PURPLE),
            vertex_dot_radius=0.04,
        )

        self.play(Create(axes), Write(labels))
        self.play(Create(graph_stop), run_time=1.5)
        self.play(Create(graph_optimal), run_time=1.5)

        legend_stop = (
            VGroup(
                Line(LEFT * 0.3, RIGHT * 0.3, color=RED),
                Text("Stop Now", font_size=20, color=RED),
            )
            .arrange(RIGHT)
            .scale(0.5)
            .to_edge(UL)
            .shift(DOWN * 0.5)
        )

        legend_opt = (
            VGroup(
                Line(LEFT * 0.3, RIGHT * 0.3, color=YELLOW),
                Text("Optimal Strategy", font_size=20, color=YELLOW),
            )
            .arrange(RIGHT)
            .scale(0.5)
            .next_to(legend_stop, DOWN, aligned_edge=LEFT)
        )

        self.play(FadeIn(legend_stop), FadeIn(legend_opt))
        self.wait(2)


class BombCandyOutro(Scene):
    def construct(self):
        # 创建居中文本，大字号
        text = Text("感谢观看", font_size=96)  # font_size 替代 scale 更稳定
        text.set_color_by_gradient(RED, GREEN, BLUE, WHITE)  # 动态渐变色

        # 动画：逐字淡入 + 轻微缩放
        self.play(
            LaggedStart(
                *[FadeIn(char, scale=3, shift=UP * 0.3) for char in text],
                lag_ratio=0.2,
                run_time=2,
            )
        )
        self.wait(1.5)


# 场景映射
SCENES = [
    ("intro", BombCandyIntro),
    ("rules", BombCandyRules),
    ("recurrence", BombCandyRecurrence),
    ("table", BombCandyTable),
    ("graph", BombCandyGraph),
    ("conclusion", BombCandyConclusion),
    ("outro", BombCandyOutro),
]


# 主流程
async def main():
    if not shutil.which("ffmpeg"):
        print("❌ 未找到 ffmpeg，请安装 ffmpeg")
        sys.exit(1)

    final_clips = []

    for key, SceneClass in SCENES:
        print(f"\n🎬 处理片段: {key}")

        # 1. 渲染无声视频
        video_path = TEMP_DIR / f"{key}_silent.mp4"
        scene = SceneClass()
        scene.render()
        # Manim 默认输出路径
        rendered = scene.renderer.file_writer.movie_file_path
        shutil.move(rendered, video_path)
        print(f"🎥 无声视频: {video_path}")

        # 2. 生成语音
        audio_path = await generate_audio_segment(key)
        print(f"🎙️ 语音文件: {audio_path}")

        # 3. 合成带音视频（最长原则）
        merged_path = TEMP_DIR / f"{key}_merged.mp4"
        merge_video_audio_with_ffmpeg(video_path, audio_path, merged_path)
        final_clips.append(merged_path)

    # 4. 拼接所有片段
    final_output = OUTPUT_DIR / "bomb_candy_final.mp4"
    concatenate_videos(final_clips, final_output)
    print(f"\n🎉 最终视频已生成: {final_output}")


if __name__ == "__main__":
    asyncio.run(main())
