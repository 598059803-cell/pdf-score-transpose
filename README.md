# PDF Score Transpose

将“上传 PDF 乐谱 → 识谱 → 指定调性移调 → 校对 → 保存西贝柳斯工程”封装成 Codex Skill 和可独立运行的本地命令行工具。

```text
PDF → Poppler 谱页 → Audiveris 识谱 → MusicXML → 指定音程移调
                                              ├─ MXL / MusicXML
                                              ├─ MuseScore → PDF
                                              └─ 本机 Sibelius → 原生 .sib
```

自动处理到可编辑 MusicXML/PDF；识谱纠错和原生 `.sib` 保存由 Agent 配合本机记谱软件完成。这里没有开源的 `.sib` 写入器，也不把改扩展名当成格式转换。

## 能做什么

- 通过目标像素尺寸规范化 PDF 谱页，避免异常物理页面尺寸带来的超大画布。
- 调用本地 Audiveris，保留 `.omr` 工程供纠错。
- 根据 OMR 的物理谱表对应关系，合并名称识别造成的重复逻辑声部；不同 `divisions` 时值单位会一起归一化。
- 按原调、目标调和方向计算音程，同时移动音符、调号和和弦根音，保留正确的升降号拼写，跳过鼓组。
- 输出声部、小节、音域和节奏检查报告，调用本地 MuseScore 导出 PDF。
- 提供本机西贝柳斯导入、原生保存与检查的 Skill 指引。

适用于印刷五线谱，尤其是合唱、声乐和钢琴总谱。OMR 仍会误读歌词、连线、震音、连音和打击乐符头，需要对照原谱校对。不会自动改编大小调、识别音频或计算吉他指法。

## 安装

需要 Python 3.11+。命令行工具安装：

```bash
python -m pip install .
scoretool --help
```

也可直接安装 Python 依赖后运行脚本：

```bash
python -m pip install Pillow
python scripts/scoretool.py --help
```

PDF 识谱还需要 [Poppler](https://poppler.freedesktop.org/)、[Audiveris](https://github.com/Audiveris/audiveris)；PDF 渲染需要 [MuseScore Studio](https://musescore.org/)。生成 `.sib` 需要本机安装可用的 [Sibelius](https://www.avid.com/sibelius)。这些工具和模型不随本仓库分发。

将 `config.example.toml` 复制为 `config.local.toml`，填写本机工具位置，然后检查：

```bash
python scripts/scoretool.py --config config.local.toml doctor
```

安装为 Codex Skill：

```bash
python scripts/install_skill.py --config config.local.toml
```

默认安装到 `CODEX_HOME/skills/pdf-score-transpose`，未设置 `CODEX_HOME` 时使用 `~/.codex/skills`。安装器只复制 Skill 运行所需的文件与显式指定的私有配置；工具都在 PATH 上时可省略 `--config`。发现同名 Skill 会停止，不覆盖既有内容。可用 `--destination` 指定完整目标目录。重开任务后调用：

> 使用 $pdf-score-transpose，把我提供的 PDF 总谱从降 B 大调移到 C 大调，保存西贝柳斯工程和 PDF。

## 使用

整条自动链路（`.sib` 保存另外进行）：

```bash
scoretool --config config.local.toml run source.pdf --work-dir work/song --from-key Bb --to-key C --direction up --languages chi_sim+eng --output outputs/song-C.mxl --render-pdf outputs/song-C.pdf
```

`--from-key` 是经谱面确认的原调。`C`/`Bb` 默认表示大调；小调用 `Am` 或 `"Eb minor"`。默认方向 `nearest` 取最邻近音程，也可选 `up`、`down`。

常规总谱允许最多七个升号或降号；超过范围的目标调或曲中调号会停止导出，不会擅自改写等音。遇到移调乐器的书写调与实音不一致，应先确认谱面，再决定如何处理；全休止的有调声部保留在总谱中并更新已有调号。配置文件支持 UTF-8（含或不含 BOM）；单步准备和整条流程的 `--pixels` 均要求 1000–10000（最长边），这不是整本长谱的内存保证。

已有可编辑 MusicXML 时直接移调：

```bash
scoretool transpose source.mxl --from-key G --to-key Bb --direction up --output outputs/song-Bb.mxl
scoretool inspect outputs/song-Bb.mxl
scoretool --config config.local.toml render outputs/song-Bb.mxl --output outputs/song-Bb.pdf
```

分步骤处理与人工纠错：

```bash
scoretool --config config.local.toml prepare source.pdf --work-dir work/song
scoretool --config config.local.toml recognize work/song/pages.tif --work-dir work/song/omr --languages chi_sim+eng
scoretool repair-parts work/song/omr/pages.mxl --omr work/song/omr/pages.omr --part-names Soprano,Alto,Bass,Cajon,Piano --output work/song/normalized.mxl
```

`run` 默认检查并合并声部别名；物理谱表变化或小节映射不一致会停止自动合并，保留识谱结果供人工处理。`--no-repair-parts` 仅适用于已经核对过原始声部映射的情况。

工具默认保留输入并拒绝覆盖已有输出。单步命令可显式使用 `--overwrite`；完整 `run` 使用新的工作目录。所有乐谱、日志和个人配置都应留在被忽略的目录中。

## 原创示例与开发检查

仓库不包含用户上传的乐谱。可生成一个原创四小节演示文件：

```bash
python scripts/make_demo.py --output work/demo-Bb.mxl
scoretool transpose work/demo-Bb.mxl --from-key Bb --to-key C --direction up --output work/demo-C.mxl
python -m unittest discover -s tests -v
```

公开仓库的自动检查面向 Windows 和 Linux、Python 3.11 和 3.12，包含安装后的命令行检查。另有两个独立的验收项：

- **本机识谱回归**：配置好上述外部工具后，设置环境变量 `SCOREFLOW_TEST_OMR=1`，运行 `python -m unittest discover -s tests -p test_demo_pipeline.py -v`。测试只用新生成的原创四小节乐谱，从 PDF 重新识别并升全音，逐一比较 32 个音的声部、谱表、起拍、时值和音高。默认使用临时目录；若要保留结果，设置 `SCOREFLOW_TEST_OUTPUT` 为一个尚不存在的测试目录。没启用此测试是“跳过”，不是“通过”。
- **本机西贝柳斯验收**：另行导入本次生成的 MXL，另存为原生工程，关闭后从交付路径重开并核对。详见[原生工程验收](references/sibelius.md)；GitHub 测试不启动商业软件，也不替代这一步。

打包 GitHub 可上传的源码与 Skill：

```bash
python scripts/package_release.py --output ../pdf-score-transpose-source.zip
```

打包器使用源码文件白名单，不包含工作目录、本机配置、软件安装包、模型、PDF、MXL、`.sib` 或日志。公开发布前仍应查看压缩包清单。

## 补充说明

- [环境配置、中文歌词和识谱校对](references/omr-and-proofreading.md)
- [西贝柳斯原生工程的交付步骤](references/sibelius.md)
- [Audiveris 命令行文档](https://audiveris.github.io/audiveris/_pages/guides/advanced/cli/)

本仓库代码使用 MIT 许可；外部软件及模型遵循各自许可。乐谱内容的使用与分发范围由其权利和用户授权决定。
