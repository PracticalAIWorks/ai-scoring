# 可配置比赛评分系统

面向手机网页的比赛评审系统，包含评委端、独立管理后台和实时排行榜。支持扫码、企业微信身份识别、整数滑块评分、自动保存，以及初试与复试成绩统计。使用 Python、SQLite 和原生 HTML/CSS/JavaScript，可通过 Docker 部署，无需单独安装数据库。

## 页面入口

| 地址 | 用途 |
| --- | --- |
| `/` | 海报首页、评委身份确认和项目评分 |
| `/admin` | 工作人员管理后台，使用管理密码登录 |
| `/leaderboard` | 独立排行榜，分模块展示，约每3秒更新 |

## 功能

- **比赛配置**：一场比赛进行中，历史比赛独立保留；可配置分类、评分大项、子项、满分、评分说明、单阶段或初复试两阶段。
- **评委端**：专属二维码、企业微信绑定或通用选择模式。评分使用整数滑块或输入框，子项自动汇总，无须点击提交；完整保存后项目卡片变绿。
- **评分规则**：内置提示词设计、解决方案设计、智能体设计，可新增分类。每个子项提供定义、核对要素和参考标准。已有评分的分值结构锁定，文字说明可编辑。
- **项目管理**：按模块新增、编辑、停用及批量导入，支持 Excel/CSV、独立模板、导入预览。项目停用保留历史评分。
- **评分汇总**：按模块切换查看，刷新保留选择；导出当前阶段全部模块的 Excel，各模块单独分表，保留每位评委的大项、子项及总分。
- **评委评分管理**：按评委、阶段和模块查找评分。修改窗口直接切换评委，分组展示大项和子项，实时汇总；支持清空单项目或该评委所选阶段全部评分。
- **批量复试与上榜**：分模块搜索、勾选、全选并批量加入或移出复试/排行榜；默认读取评委评分，也支持手动成绩。
- **实时排行榜**：选择展示模块、项目和计分方式，支持仅初试、仅复试、初复试加权及手动最终分。排行榜只显示排名、主题、姓名、部门、最终分数。
- **大屏滚动**：后台设置速度、榜首停留和底部停留；慢速向上滚至第一名，固定标题和表头。实时更新保留滚动位置。
- **现场宣讲**：后台设置当前项目、上一项、下一项；评委项目列表整卡黄色高亮，不强制跳转或弹出悬浮入口。
- **后台布局**：顶部和菜单固定，内容区独立滚动；启停、编辑保存和刷新后保留滚动位置。支持手机访问。
- **海报**：内置 AVIF/WebP 多分辨率图片和长期缓存，后台可上传自己的海报。

## 统计规则

1. **只有启用评委参与自动平均分和排行榜。** 停用评委的评分保留为历史明细，在 Excel 中标注，但不计入均分或完整评委数。
2. 自动排行榜须所有启用评委都完整评完同一项目后才发布成绩；没有启用评委时不会发布。缺任一评分项时显示“待评分”，0分有效。
3. 初试和复试独立保存。默认加权为初试70%＋复试30%，后台可调整，两项合计100%。仅使用某一阶段或某阶段权重为0时，无须等待另一阶段。
4. 手动阶段成绩、手动最终分按照后台录入值生效，不会因停用评委而自动重算。原始评委评分仍保留。
5. 全部启用评委完成后，修改评分会自动更新排行榜；清空必要评分后恢复待评分。重新启用评委后，其完整评分恢复计入，未完成则重新等待。
6. 汇总页和评分明细 Excel 可查看已完成启用评委的暂定均分；**大屏排行榜**会严格等待全体启用评委完成。加权计算不提前舍入，最终保留两位小数；同分按1、1、3并列。
7. 上榜、复试资格和项目启用是独立设置。批量加入复试不会自动切换当前评分阶段。

### 管理员改分

打开“评委评分”，选择评委、初试/复试和模块，点击项目的“查看 / 修改”。窗口顶部可直接切换其他评委，每次只修改当前评委、本项目、所选阶段的评分。

- 修改评分填写原因；清空评分只需确认，**无需填写原因**。
- 子项自动汇总，留空为未评分；历史整项评分沿用原方式编辑。
- 修改前后数据自动记入各比赛数据库的 `score_admin_log`，页面可查看最近50条记录，数据库备份同时保留日志。
- 系统检查评分规则版本和原评分值；其他人已改分时拒绝覆盖，需重新打开。
- 管理前通知评委暂停操作，避免其后续主动改分再次覆盖。未保存时切换评委会提示是否放弃修改。

## 本地启动

要求 Python 3.12 或更新版本。

```bash
python -m pip install -r requirements.txt
```

复制 `.env.example` 为 `.env`，设置自己的 `ADMIN_PASSWORD`，再运行：

```bash
python app.py
```

默认端口8000。Windows 也可双击 `start-preview.cmd`，或运行 `start-preview.ps1`。通过 `HOST`、`PORT`、`DATA_DIR` 环境变量更改监听地址、端口和数据目录。

手机须使用电脑的可达局域网地址，不能使用手机自身的 `127.0.0.1`。公网使用建议部署到服务器。

## Docker 首次部署

服务器安装 Docker 和 Docker Compose 插件，克隆仓库：

```bash
git clone https://github.com/PracticalAIWorks/ai-scoring.git
cd ai-scoring
cp .env.example .env
chmod 600 .env
```

编辑 `.env`，自行设置密码。以下是公网 HTTP 8089 示例，企业微信参数按需填写：

```dotenv
ADMIN_PASSWORD=replace-with-your-own-long-password
BIND_ADDRESS=0.0.0.0
APP_PORT=8089
COOKIE_SECURE=0
WECOM_CORP_ID=
WECOM_AGENT_ID=
WECOM_SECRET=
WECOM_PUBLIC_URL=http://score.example.com:8089
```

```bash
docker compose -p ai-scoring up -d --build
docker compose -p ai-scoring ps
curl --noproxy '*' -i http://127.0.0.1:8089/
```

云安全组和服务器防火墙需允许实际网页端口。访问 `http://服务器地址:8089/admin`，录入评委、项目、评分规则和海报。

HTTPS 部署使用同机 Nginx 时，保留 `BIND_ADDRESS=127.0.0.1`、`APP_PORT=8000`、`COOKIE_SECURE=1`，参考 [deploy/nginx.conf](deploy/nginx.conf)，替换域名和证书路径，并将 `WECOM_PUBLIC_URL` 设为实际 HTTPS 根地址。

`.env` 由 Compose 传入容器；Python 本地运行也会读取其中的管理密码和企业微信参数。`.env` 不得提交仓库。

## 更新已有服务器：先备份，再替换

**保留原 `.env`、`compose.yaml` 和数据卷；不要执行 `docker compose down -v`，不要用本地测试数据库覆盖服务器。** 固定 Compose 项目名 `ai-scoring`，继续挂载原数据卷 `ai-scoring_competition_data` 到 `/data`。

更新前暂停评分，以下命令在同一个终端执行。示例原目录为 `$HOME/ai-scoring`，新部署包上传为 `$HOME/ai-scoring-update.zip`。

### 1. 备份原程序和配置

```bash
cd "$HOME/ai-scoring"
SCORING_BACKUP_DIR="$HOME/ai-scoring-backups/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$SCORING_BACKUP_DIR"
chmod 700 "$SCORING_BACKUP_DIR"
tar --exclude='./data' --exclude='./backups' --exclude='./.git' -czf "$SCORING_BACKUP_DIR/program-before-update.tar.gz" .
```

### 2. 备份全部比赛数据库和上传文件

兼容旧容器没有新版 `backup.py` 的情况：

```bash
unzip -p "$HOME/ai-scoring-update.zip" backup.py > "$SCORING_BACKUP_DIR/backup.py"
docker compose -p ai-scoring cp "$SCORING_BACKUP_DIR/backup.py" scoring:/tmp/scoring-update-backup.py
docker compose -p ai-scoring exec -T -e DATA_DIR=/data scoring python /tmp/scoring-update-backup.py /data/backups
docker compose -p ai-scoring cp scoring:/data/backups "$SCORING_BACKUP_DIR/database-backups"
```

确认输出 `Backup saved` 且备份已复制成功，再继续。每一步报错均应停止，保留原服务进行排查。

### 3. 覆盖程序，保留部署配置

```bash
docker compose -p ai-scoring stop scoring
unzip -o "$HOME/ai-scoring-update.zip" -d "$HOME/ai-scoring" -x '.env' 'compose.yaml'
docker compose -p ai-scoring up -d --build
docker compose -p ai-scoring ps
curl --noproxy '*' --retry 10 --retry-connrefused --retry-delay 2 -i http://127.0.0.1:8089/
```

按实际端口调整检查地址。返回200后，后台强制刷新，确认当前比赛、原项目、评分、海报及企业微信绑定，再恢复使用。升级会自动新增必要表，不会自动切换比赛或评分阶段。若保留的是更旧的 Compose 文件，还须确认它已传入所需的企业微信环境变量；合并方式见企业微信接入说明。

已有 Git 工作副本也可在备份后使用 `git pull --ff-only`，保留部署配置及数据卷，再重建容器；若部署时修改了已跟踪的配置文件，先备份并处理差异，不使用强制重置覆盖。

## 数据备份与迁移

完整备份包含：

```text
scores.sqlite3             原比赛、共享管理会话
competitions.sqlite3       比赛目录和当前比赛
competitions/*.sqlite3     各历史比赛独立数据
media/                     上传海报
wecom-verification/        已上传的企业微信域名校验文件（如有）
```

`backup.py` 使用 SQLite 在线备份接口，不直接复制活动数据库文件；**不能只迁移 `scores.sqlite3`**。管理评分日志、企业微信绑定、个人入口密钥也在数据库中。

```bash
docker compose -p ai-scoring exec -T scoring python backup.py /data/backups
```

迁移到新服务器时，停止目标评分服务，将同一个时间戳目录的完整内容恢复到目标 `/data` 卷，保留对应 `.env` 和部署设置，并确保容器UID10001可读写。不要混用多个时间点的数据。备份含身份与评分数据，应存入受限目录。

## 企业微信与二维码

- 个人专属二维码由后台本地生成，扫码先打开海报首页，姓名固定；改名保留入口，重置或停用撤销旧入口。更换实际评委请新增人员，避免混用原评分。
- 通用模式允许未绑定用户选择启用评委；绑定模式只允许有效企业微信绑定或专属入口。切换模式不删除原绑定。
- 企业微信通过OAuth获取UserID并绑定已有评委，不按姓名猜测身份。企业应用配置、可信域名、可信IP、校验文件、成员可见范围及服务器Secret见 [企业微信接入说明](企业微信接入说明.md)。
- 校验文件请自行下载并通过后台上传，确认域名要求的端口能返回原文件；实际Secret和校验文件不放入公开仓库。
- 专属二维码是持有者凭证，请分别发送本人；手机扫码网址必须是手机可访问的实际地址。

## 测试

后端测试使用临时数据库，不修改正式比赛数据：

```bash
python -m unittest discover -s tests -p test_app.py -v
```

当前71项测试涵盖身份校验、评分范围、并发版本、初复试隔离、动态比赛规则、数据导入、完整评委发布条件、停用排除、管理员改分/清空、操作记录、排行榜和Excel。

浏览器测试要求 Node.js、Playwright 和 Microsoft Edge；脚本默认通过 `require('playwright')` 加载，也可设置 `PLAYWRIGHT_MODULE` 和 `TEST_PYTHON` 指向安装位置。各脚本启动独立端口和临时数据目录。

| 脚本 | 验证内容 |
| --- | --- |
| `tests/ui_e2e.cjs` | 手机评审、二维码、自动保存及后台基本操作 |
| `tests/finals_e2e.cjs` | 复试资格、阶段切换和加权 |
| `tests/presentation_e2e.cjs` | 当前宣讲高亮和项目模块管理 |
| `tests/wecom_e2e.cjs` | 企业微信入口及绑定模式 |
| `tests/competition_e2e.cjs` | 比赛切换、配置和历史隔离 |
| `tests/ranking_e2e.cjs` | 排行榜配置和不同分数来源 |
| `tests/leaderboard_scroll_e2e.cjs` | 大屏循环滚动与实时刷新 |
| `tests/batch_finals_e2e.cjs` | 分模块批量复试及上榜 |
| `tests/admin_scroll_e2e.cjs` | 汇总模块切换、后台滚动位置和启停操作 |
| `tests/admin_scores_e2e.cjs` | 多评委修改窗口、子项合计、清空与阶段隔离 |

```bash
node tests/admin_scores_e2e.cjs
```

## 常见问题

| 现象 | 排查方法 |
| --- | --- |
| 容器反复重启 | `docker compose -p ai-scoring logs --tail=80 scoring` |
| 容器刚启动，连接被重置 | 稍候后重试，并检查容器状态与日志；`Started`不等于应用已通过健康验证。 |
| `curl` 返回400 | 用 `curl --noproxy '*' -i http://127.0.0.1:8089/` 查看响应正文，再检查日志。确认命令独立执行、URL完整、没有将其他文字粘入命令。不要仅凭400判断数据丢失或部署成功。 |
| 本机200，公网超时 | 检查绑定地址、云安全组、防火墙及访问端口。SSH可连接不代表网页端口已开放。 |
| 后台登录后失效 | HTTP需 `COOKIE_SECURE=0`，HTTPS使用安全Cookie；更改后重建容器。 |
| 评分已填但排行榜待评分 | 检查全部启用评委是否完成、所选阶段/权重、项目是否上榜、模块是否展示，以及手动分是否为空。 |
| 停用评委仍影响分数 | 确认部署新版；自动来源排除停用评委，手动录入成绩需管理员自行调整。 |
| 企业微信回调报可信域名错误 | 按企业微信接入说明核对应用、完整回调网址及可信域名设置。 |
| 手机无法访问本机地址 | 使用可达的电脑局域网IP或服务器域名，不使用手机的 `127.0.0.1`。 |
| 页面仍是旧布局 | 电脑Ctrl+F5；手机关闭旧页面后重新进入。 |

## 文件与更多说明

- `app.py`：WSGI应用、身份验证、评分、排行榜及管理接口。
- `competition.py`、`event_rules.py`：比赛隔离和可配置评分规则。
- `project_import.py`、`excel_export.py`：项目导入和Excel导出。
- `wecom_auth.py`：企业微信授权集成。
- `backup.py`、`deploy/`：数据库备份、Nginx示例、企业微信配置合并工具。
- `static/`：评委端、后台、排行榜及本地二维码组件。
- [部署交接说明](部署交接说明.md)、[通用比赛后台说明](通用比赛后台说明.md)、[评分子项细则](评分子项细则.md)。

数据库、上传文件、真实配置、个人二维码、测试输出和本地开发环境不提交仓库。内置二维码组件 qrcode-generator 1.4.4 采用MIT许可，详见 `static/vendor/`。代码仓库更新不等于正式服务器已部署，服务器需单独更新与验收。
