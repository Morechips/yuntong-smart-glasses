# 本地整理包与 GitHub 提交

本目录是已发布仓库的根目录：[Morechips/yuntong-smart-glasses](https://github.com/Morechips/yuntong-smart-glasses)。本地提交副本位于 `releases/github_submission_20261103/repository/`；不要在原始 `D:\智能眼镜` 工作区直接 `git add .`。

## 已做的整理

- 按明确文件清单复制主程序、训练 / 量化源码、必要模型与 32 张开发图。
- 收录当前组合的 STEP、STL、3MF 和装配图；生成模型的脚本、参数及 CAD 开发环境不随包附带，历史组件替换关系见建模说明。
- ESP 只放脱敏源码和锁文件，不放编入本地 Wi-Fi 的固件。
- 排除密码文件、SSH 辅助凭据、`sdkconfig.local`、虚拟环境、原始采集视频、设备日志、个人申报表和本机工具链。
- 补充可配置设备入口、离线模型检查、校验脚本及说明文档。

具体来源、导出改动和文件哈希见 `submission/`。本项目自有代码授权尚未指定，未擅自添加 MIT / Apache 等许可证；如需公开开源，可随后由团队决定授权。

文本换行已与 `.gitattributes` 对齐，供克隆后继续使用同一份哈希清单；CMD / PowerShell 为 CRLF，其余文本 LF，模型和制造文件按二进制处理。

## 查看和更新仓库

1. 阅读根 README 和 `docs/STATUS.md`，核对作品状态表述。
2. 运行核验和离线演示；联网硬件测试另按使用说明安排。
3. 后续修改从本目录继续，提交前检查差异并核对文件清单。

```powershell
git status --short --branch
python scripts/verify_repository.py
git add .
git status --short
git diff --cached --stat
git diff --cached --check
git commit -m "Describe your change"
git push -u origin main
```

本地仓库已配置 `origin`，跟踪远端 `main`。多人协作时先拉取远端变更并解决冲突；不要强制覆盖远端。

## 后续提交前

`python scripts/verify_repository.py` 会检查交付清单哈希、必要文件、JSON、Python 语法、文档本地链接、禁止文件与内部大小阈值。哈希变化表示与这次提交快照不同；修改后应保留新变更并按实际内容更新清单，不能为通过检查恢复覆盖自己的工作。

`.gitignore` 只屏蔽运行产物和本地配置，不屏蔽已随包收录的模型 BIN / ONNX、模型一致性样本、结构文件。新增数据和固件前应单独判断是否含隐私或密钥。任何自动扫描都只是一组具体检查，不代表全部风险已被穷尽。
