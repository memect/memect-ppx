<!-- markdownlint-disable MD024 -->
# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [0.2.10] - 2026-06-11

### ✨ Features

- 完善脚注解析，支持跨页表格合并、跨页段落合并
- 初步支持脚注（footnote）识别

### 🐛 Fixes

- 修正有边框表格水平线未对齐的问题
- 处理由大量小图片组成虚线的情况

### ⚡ Performance

- 优化海量小图片下的解析速度

### 🔨 Chore

- Merge dev/github into github-main-clean

## [0.2.9] - 2026-06-09

### 🔨 Chore

- 添加一键安装脚本 install.sh / install.py

## [0.2.8] - 2026-06-06

### ✨ Features

- Add parser classes for PDF chapter structure, outlines, and table of contents
- Implement chapter tree parsing from PDF outlines and TOC

### 📌 Miscellaneous

- 对于对象识别错误的表格，进行了修正
- 修正之前线清除的问题，没有使用转换后的坐标
- 完善了pdf表格的线的计算
- 修改因为表格严格检查引起表格header/body的重新计算的问题
- 完善了wingdings的识别
- 修复无边框单元格区域溢出
- 对于pdf表格存在双层边界线的问题，进行了优化
- 去掉命名为联合资信的字典文件
- 初步支持跨页表格合并
- 提供了实验下的章节模版生成
- 生成tree.md，也初步支持通过agent来快速满足需求
- 完善了xtree_llm.py
- 去掉了不必要的代码
- 初步完成了章节树的3个分析模式

### 📝 Documentation

- Update section title from '安装' to 'UV安装' in README_zh-CN.md

### 🔨 Chore

- Merge dev/github into github-main-clean
- Remove .claude/ from git tracking

## [0.2.7] - 2026-05-22

### 📌 Miscellaneous

- Merge branch 'dev/github' into github-main-clean
- Merge branch 'main' into dev/github
- 完善了wbk

### 📝 Documentation

- Update CHANGELOG for 0.2.7

## [0.2.6] - 2026-05-21

### ✨ Features

- Add new two-column PDF samples for Chinese and English
- Update dependencies and version to 0.2.6
- Support two cols.

### 🐛 Fixes

- Enhance jsonify method precision and adjust cell handling in Parser

### 📌 Miscellaneous

- Merge branch 'dev/github' into github-main-clean
- Merge branch 'main' into dev/github
- 完善了ocr字符识别，也就是bbox的计算
- Merge branch 'main' into dev/github
- 解决无边框表格的问题
- 添加依赖
- Merge branch 'dev/github' into github-main-clean
- 修正输出
- 完善分栏
- 修改一个重构后的import
- 完成了分栏

## [0.2.5] - 2026-05-15

### ✨ Features

- **skill**: Rename to memect-ppx, bump to 0.2.5, add --html option

### 📌 Miscellaneous

- Merge branch 'dev/github' into github-main-clean
- [fix] Comment out Tree initialization and event listener in doc.js
- Merge branch 'main' into dev/github
- 去掉了KMarkdown，使用KText，也支持渲染为html
- Merge remote-tracking branch 'origin/github-main-clean' into github-main-clean

### 📝 Documentation

- Update CHANGELOG for 0.2.5
- Add --html usage examples

### 🔨 Chore

- Rename skill dir ppx-parse → memect-ppx, improve search discoverability
- Update release command to dual-scenario format
- Track .claude/commands and update gitignore
- Ignore scripts/ directory
- Add .claude/commands for internal dev workflow
- Track .claude/commands and update gitignore
- Ignore scripts/ directory
- Clean up redundant entries in CHANGELOG
- Track release scripts and auto-update CHANGELOG on tag

## [0.2.4] - 2026-05-09

### ✨ Features

- Add formula parsing modules and refactor FormulaModel

### 📌 Miscellaneous

- Merge branch 'main' into dev/github
- 完善cli，以便公式可以不解析
- 添加了pp的formula模型
- Ai debug
- Ai debug
- Ai debug
- Ai debug
- Ai debug
- Ai debug
- 修复模型中gpu下的异常
- 修复FormulaPP的异常
- 添加日志
- 添加modelscope
- 使用PP的公式模型，速度更快
- 更新了readme.md
- 使用新的公式模型

### 📝 Documentation

- Update CHANGELOG for 0.2.3

### 🔨 Chore

- Bump version to 0.2.4

### 🤖 CI/CD

- Add --system flag to uv pip install for GitHub Actions
- Build first, then verify from local dist before release
- Checkout tag commit instead of hardcoded main branch

## [0.2.3] - 2026-05-06

### 🐛 Fixes

- Update homepage URL in project metadata

### 📌 Miscellaneous

- Merge branch 'main' into dev/github
- 简化了安装
- 调整cuda的包
- 更新config
- 更好的支持windows
- 格式化代码
- 删除不需要的代码

### 🔨 Chore

- Bump version to 0.2.3 and simplify README install docs

## [0.2.2] - 2026-04-28

### 🔨 Chore

- Bump version to 0.2.2

## [0.2.1] - 2026-04-28

### ✨ Features

- **skill**: 启用 user-invocable 支持斜杠调用

### 📌 Miscellaneous

- Merge branch 'dev/github' into github-main-clean
- Merge branch 'main' into dev/github
- 完善了service
- 修改了api接口
- 修改了多进程的处理方式
- 支持去掉Textbox中空白的行
- 添加日志，调试ocr的解析时间
- 修改参数
- 修改配置
- 添加日志
- 完善下载
- 提供命令，先下载模型，避免多线程/多进程同时下载
- 去掉不必要的import
- 增强了版面分析等

### 📝 Documentation

- Add Star History section to README files
- **skill**: 优化 ppx-parse skill description，提高触发命中率

### 🔨 Chore

- Bump version to 0.2.1
- **skill**: 同步 memect-ppx 版本至 0.2.1

## [0.2.0] - 2026-04-24

### ♻️ Refactor

- Restructure table parsing pipeline and bump version to 0.1.0
- 重构表格解析模块，合并 dev/github 分支

### ✨ Features

- Add ppx-parse skill
- Add table parsing instructions to README_zh-CN.md

### 📌 Miscellaneous

- Merge branch 'dev/github' into github-main-clean
- Merge branch 'main' into dev/github
- 解决了没有字符覆盖的透明图片没有转换为正确对象的问题
- Merge branch 'github-main-clean' of 192.168.0.24:idp/docparser/ppx into github-main-clean

# Conflicts:
#	src/memect/pdf/default/table/llm.py
- Merge branch 'dev/github' into 'github-main-clean'

Dev/github

See merge request idp/docparser/ppx!2
- Merge branch 'github-main-clean' into 'dev/github'

# Conflicts:
#   .gitignore
#   src/memect/pdf/default/table/llm.py
- 更新文档
- 补充文档说明，deepseek-ocr-2不能够使用vllm==0.19.1
- 完成了table=auto的解析，调整了命了行参数
- 初步完成了无边框和llm的解析

### 📝 Documentation

- 添加 pip 升级安装命令
- Add CHANGELOG.md and git-cliff configuration
- Clean up README duplicates and update LLM service instructions

### 🔨 Chore

- 移除废弃的 _wtable.py 旧表格解析模块
- Bump version to 0.2.0
- Add skills/ppx-parse to gitignore whitelist
- Disable image display in anchor drawing for cleaner output
- Update license and related documentation references
- Bump version to 0.0.2

### 🤖 CI/CD

- 使用 git-cliff 生成 Release Notes

## [0.0.2] - 2026-04-22

### ⏪ Revert

- Restore direct cv2 import in images.py to match main

### ♻️ Refactor

- Lazy-import cv2 under TYPE_CHECKING in images.py

### ✨ Features

- Add example PNG files for table color variations
- Add borderless table and LLM parsing support

### 🐛 Fixes

- Remove redundant header from README.md
- Update discussion URLs in issue template to point to the correct repository

### 📝 Documentation

- Add libGL.so.1 FAQ entry for Linux server environments

### 🔨 Chore

- Add example PDFs and expose ppx module in package config
- Add setuptools package-data config for web, pdf assets
- Remove old issue templates and add new ones for bug reports and feature requests

## [0.0.1.post2] - 2026-04-20

### 🐛 Fixes

- Remove deprecated license classifier to fix sdist build

### 📌 Miscellaneous

- Merge pull request #3 from memect/github-main-clean

docs: update installation instructions for clarity and add activation…
- Merge pull request #2 from memect/github-main-clean

GitHub main clean
- 修改了一个表格解析的错误
- 做了调整，以便更好的支持作为sdk使用
- 在删除子进程的时候，跳过resource_tracker.py
- ModelExecutor自动释放
- 调整了模型的配置，更方便
- 先提交给测试
- 提供了batch操作
- 添加README.md
- Initial commit
- Initial commit

### 📝 Documentation

- Update installation instructions for clarity and add activation step

### 🔨 Chore

- Update project files and licensing metadata

### 🤖 CI/CD

- Install opencv before CLI verification

<!-- generated by git-cliff -->
