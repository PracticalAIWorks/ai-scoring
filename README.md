# AI 创新应用大赛评分系统

手机网页评审端 + 独立管理后台。可部署到公司提供的服务器，由 IT 接入域名和 HTTPS。不依赖微信小程序、第三方账号或外部数据库服务。

## 已实现

- `/`：扫码打开首页，选择评委姓名后进入，无登录、无提交按钮。
- 三个项目分类：提示词设计、解决方案设计、智能体设计。
- 项目卡片显示序号、主题、参赛人、部门、评分进度。完整评分保存成功后卡片变绿，可反复修改。
- 评分页展示项目主题、参赛人和部门，下面按评分表逐项打分，不展示项目基础介绍。后台新增项目也无需填写介绍。支持 0 分、两位小数及清空，校验单项上限。
- 输入后约 450ms 自动保存到服务器。有效但尚未同步的修改暂存在当前浏览器，断网后自动重试，重新打开同一浏览器可继续同步。页面显示保存状态；看到“已保存到服务器”才表示已写入服务器。
- `/admin`：单独的工作人员入口。管理评委、参赛项目、首页文案和整站背景海报；查看评分明细及进度，导出 `.xlsx`。
- SQLite 数据库集中保存评分；服务器重启不会清空。停用人员、项目保留历史评分。

## 海报加载

内置海报提供 960 / 1600 像素两档，优先 AVIF，不支持时使用 WebP。原 PNG 约 9.7MB，优化后的单张约 66–199KB；浏览器根据屏幕和像素密度选择尺寸。版本化图片启用长期缓存，HTML、接口和评分仍不缓存。旧数据库中的内置 PNG 地址自动映射到压缩版本，自定义上传的海报保持原地址。后台上传建议选用压缩 WebP。

## 评分标准与统计口径

来自用户提供的《2026创新大赛评分表.xlsx》，配置在 `rubrics.json`。

| 分类 | 各评分项满分 | 总分 |
| --- | --- | --- |
| 提示词设计 | 25 / 25 / 20 / 20 / 10 | 100 |
| 解决方案设计 | 30 / 25 / 25 / 20 | 100 |
| 智能体设计 | 25 / 25 / 25 / 20 / 5 | 100 |

智能体按用户确认采用表头分值，不采用表下说明中的分值。说明文本仅保留评价内容。

导出包含 **提示词设计、解决方案设计、智能体设计** 三张模块总表。每张表顶部按项目平均总分从高到低列出排名、项目、人员、部门及各评委总分；点击项目名称跳到同表下方分项明细。下方保留每位评委的实际评分点得分、项目平均分和模块评分细则。不冻结窗格。同分按竞赛排名并列（例如 1、1、3）；无完整评分的项目放在后面且不排名，停用项目保留记录但不排名。未全部评完时排名为暂定。

每个项目的所有维度都填写后才算完整；未填写与 0 分不同。未完整评分不参与平均，完整评分总分保留两位小数，平均分保留两位小数。项目平均分包含该项目所有完整历史评分（包括已停用评委），分类统计和进度只针对当前启用评委及项目。停用状态会在导出中标注。

## 本地启动

安装 Python 3.12 或更新版本。开发预览不需要安装依赖：

```powershell
python app.py
```

打开 `http://127.0.0.1:8000/`。后台地址 `http://127.0.0.1:8000/admin`，未设置 `ADMIN_PASSWORD` 时，启动窗口显示临时管理密码（重启会变化）。如需固定，在启动前通过环境变量设置。

初次启动会生成 **3 位演示评委、6 个演示项目**，参赛人和评委姓名标为演示。正式使用前请在后台替换或停用演示内容，添加真实资料、上传海报。开发测试使用独立临时数据库，不会写入正式数据。

## 服务器部署

需要服务器、Docker Engine 和 Docker Compose 插件，以及运行 Docker 和配置访问端口的权限。可以直接通过公网 IP 和 HTTP 端口使用，也可以接入域名及 HTTPS。系统部署到根路径，暂不支持 `/score/` 这样的子路径。

### 方式一：公网 IP + HTTP 8089

先检查环境。以下命令每个代码框单独执行，等待返回后再执行下一条。

```bash
docker --version
```

```bash
docker compose version
```

缺少命令或 Docker 权限不足时，先由服务器管理员配置。构建时需要联网下载 Python 基础镜像及依赖。

将仓库克隆到新目录；私有仓库需要使用具有访问权限的 GitHub 账号认证。

```bash
git clone https://github.com/PracticalAIWorks/ai-scoring.git "$HOME/ai-scoring"
```

```bash
cd "$HOME/ai-scoring"
```

如果已经上传部署 ZIP，则在此目录解压即可，不用再次克隆。已有同名目录时，不要覆盖其 `.env` 和数据。

```bash
cp .env.example .env
```

```bash
vi .env
```

将 `.env` 设置为下面格式，替换密码占位符。已有服务器密码保留在服务器本地，仓库不保存实际密码。

```dotenv
ADMIN_PASSWORD=替换为你的后台密码
BIND_ADDRESS=0.0.0.0
APP_PORT=8089
COOKIE_SECURE=0
```

```bash
chmod 600 .env
```

```bash
docker compose -p ai-scoring up -d --build
```

```bash
docker compose -p ai-scoring ps
```

```bash
curl -I http://127.0.0.1:8089/
```

容器状态应为 `Up`，HTTP 应返回 `200 OK`。刚启动的瞬间可能尚未就绪，等待几秒后重试。然后在云平台安全组及服务器防火墙中放行 TCP 8089 入站访问。

- 评委端：`http://服务器公网IP:8089/`
- 管理后台：`http://服务器公网IP:8089/admin`

使用手机流量验证网址，再将此网址生成二维码。HTTP 模式必须设置 `COOKIE_SECURE=0`，否则后台登录会话无法保持。HTTP 不加密传输密码和评分。

### 方式二：域名 + HTTPS

复制 `.env.example` 为 `.env`，设置后台密码，保持 `BIND_ADDRESS=127.0.0.1`、`APP_PORT=8000`、`COOKIE_SECURE=1`，运行 `docker compose -p ai-scoring up -d --build`。

应用只监听服务器本机 8000 端口。参考 `deploy/nginx.conf` 配置域名和证书，由同机 Nginx 转发到 `http://127.0.0.1:8000`，对外开放 80/443。域名、证书及适用的备案接入安排由公司管理员处理。若代理在另一台服务器，绑定应用所在服务器的内网 IP，并限制端口来源为代理服务器。

评委入口为 `https://你的域名/`，后台为 `https://你的域名/admin`。评委页面不显示后台入口。

以上命令固定使用 Compose 项目名 `ai-scoring`，持久数据卷的完整名称为 `ai-scoring_competition_data`，容器中挂载到 `/data`。更新、备份时继续使用同一项目名。不要执行 `docker compose down -v`，否则会删除数据库和上传海报。

### 方式三：直接运行 Python

```text
python -m venv venv
# 激活 venv 后：
python -m pip install -r requirements.txt
```

设置环境变量 `ADMIN_PASSWORD`、`DATA_DIR`（持久目录的绝对路径）、`COOKIE_SECURE=1`，然后执行：

```text
waitress-serve --listen=127.0.0.1:8000 --threads=8 app:application
```

用 systemd 或 Windows 服务管理器保证开机自启、崩溃重启。开发自带的 `python app.py` 不作为生产启动方式。反向代理请保留原始 Host。`COOKIE_SECURE=1` 用于 HTTPS；HTTP 模式设为 0。

## 日常管理与代码更新

评委、参赛项目、主题、姓名、部门、启用状态、首页海报和评分导出在 `/admin` 管理。当前不提供 Excel 批量导入入口。页面布局、评分规则配置、程序逻辑需要更新代码后重新部署。

在服务器项目目录依次执行：

```bash
git pull --ff-only
```

```bash
docker compose -p ai-scoring up -d --build
```

`.env` 和持久数据卷不会随 Git 更新覆盖。原先手动修改过受版本管理文件的服务器，先核对本地修改，不要直接丢弃配置。修改 `.env` 中的密码后执行 `docker compose -p ai-scoring up -d --force-recreate` 让容器重新读取环境变量。

## 备份与更换服务器

Docker 部署时，在旧服务器项目目录执行以下命令生成一致的 SQLite 备份和上传海报副本：

```bash
docker compose -p ai-scoring exec -T scoring python backup.py /data/backups
```

```bash
mkdir -p backups
```

```bash
docker compose -p ai-scoring cp scoring:/data/backups/. ./backups/
```

每个时间戳目录包含 `scores.sqlite3`，以及存在时的 `media/`。迁移前暂停评委使用，完成最后一次备份，避免备份后继续产生新评分。

新服务器首次启动会创建独立数据库，**代码上传不会带走旧服务器的名单和评分**。将旧服务器的时间戳备份目录上传到新服务器项目目录，改名为 `restore-source`。确认其中直接包含 `scores.sqlite3`，而不是再嵌套一层时间戳文件夹。

以下恢复操作会替换新服务器已有数据库，应在新服务器尚未开始正式评分时执行；如果已有新评分，先备份，不能直接合并两套评分。

```bash
docker compose -p ai-scoring stop scoring
```

```bash
docker compose -p ai-scoring cp ./restore-source/scores.sqlite3 scoring:/data/scores.sqlite3
```

备份含 `media` 目录时执行：

```bash
docker compose -p ai-scoring cp ./restore-source/media/. scoring:/data/media/
```

修复复制文件的拥有者，再启动：

```bash
docker run --rm --user 0 --entrypoint sh -v ai-scoring_competition_data:/data ai-scoring-scoring -c 'chown -R 10001:10001 /data'
```

```bash
docker compose -p ai-scoring up -d
```

新旧服务器保持相同的项目名 `ai-scoring`。旧数据保留到新服务器验证完成后再处理。

## 常见问题

| 情况 | 检查方法 |
| --- | --- |
| `unzip` 找不到文件 | 执行 `pwd` 和 `ls -lh`，确认 ZIP 所在目录；FinalShell 文件面板目录可能与终端不同。 |
| 容器反复重启 | `docker compose -p ai-scoring logs --tail=80 scoring` 查看报错。 |
| 本机访问失败 | 检查容器状态、日志和 `.env` 中的 `APP_PORT`；HTTP 8089 模式用 `curl -I http://127.0.0.1:8089/`。 |
| 服务器本机返回 200，公网超时 | 检查绑定地址是否为 `0.0.0.0`、云安全组、服务器防火墙及公网 IP。SSH 22 端口可连通不代表网页 8089 已开放。 |
| 后台无法保持登录 | HTTP 模式确认 `COOKIE_SECURE=0`，修改后重新创建容器。 |
| 新服务器没有正式项目 | 新服务器是新数据库，需要后台录入或恢复旧服务器备份。 |
| 手机打不开电脑的 `127.0.0.1` | 此地址指手机自身。使用可达的电脑局域网 IP 或服务器公网地址。 |

## 数据与权限

- 评委端按用户要求无身份验证：任何能访问网站的人都能选择名单中的姓名并修改该评委评分。适用于受控分发的内部比赛，不保证实名身份。默认每位评委独立评分；同一评委在多台设备同时改同一项时，以最后到达服务器的有效修改为准。
- 管理后台需要管理密码，密码由服务器环境变量提供。管理会话 12 小时，HttpOnly Cookie；不在评委端暴露导出接口。请勿在生产环境使用演示密码。
- 服务器数据目录默认为 `data/`，包括数据库、上传海报。不要公开此目录，也不要开放数据库端口。
- 比赛前后备份数据库及海报。运行时可用 Python `sqlite3.Connection.backup()` 生成一致的数据库备份；不要只复制正在写入的 `.sqlite3` 文件而忽略 WAL。可使用 `python backup.py <备份目录>`。
- 输入分数后请确认显示“已保存到服务器”。网络中断时不要清除浏览器数据；恢复网络后待同步提示消失再退出。没有任何程序能保证浏览器数据被清除或服务器硬盘损坏后仍恢复未备份数据。
- 本版按单场比赛设计，不含多赛事切换。修改已评分项目的名称、人员或部门会影响当前展示及导出，正式比赛开始后请固定项目资料。

## 验证

```text
python -m unittest discover -s tests -v
```

测试覆盖分数范围、0 分与清空、保存恢复、独立评委、完整性与平均值、后台访问控制、编辑限制和 Excel 文件结构。
