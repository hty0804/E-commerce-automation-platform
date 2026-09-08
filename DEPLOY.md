# 部署到在线地址（Render / Railway / Hugging Face Spaces）

前端是**纯静态站点**（`index.html` + `assets/`），无需后端、无需构建、无外部接口调用
（演示模式全部在浏览器内用 localStorage + 模拟引擎跑）。因此它可以托管到任意静态平台，
得到一个公开可访问的网址。部署后演示账号仍为 `admin / admin123`。

> ⚠️ 安全提示：当前为演示系统，登录密码固定为 `admin/admin123`，且前端**不保存任何真实平台密钥**
> （真实密钥只在 `python_backend/` 的 Python 后端使用，见仓库内「部署指南」页）。公网部署前请知悉这是演示用途。

---

## 方式一：Hugging Face Spaces（最省事，免费 + 免信用卡，约 1 分钟拿到网址）

不需要任何配置文件，HF 会自动生成带 frontmatter 的 README。

1. 打开 https://huggingface.co/spaces → 点 **Create new Space**
2. 填名称（如 `ecommerce-sop-admin`），**SDK 选 `Static`**，可见性选 **Public**
3. 创建后进入文件列表，点 **Add file** → 上传本仓库根目录的 `index.html`
4. 再上传整个 `assets/` 文件夹（直接拖进去即可）
5. 等十几秒，访问 `https://<你的用户名>-<space名>.hf.space` 即可

---

## 方式二：Render（关联 GitHub → Static Site）

前提：先把本仓库 push 到你的 GitHub（仓库已 `git init` 并提交初始 commit，见 README「上传到 GitHub」）。

1. Render 控制台 → **New** → **Static Site** → 关联 GitHub 仓库
2. Build Command 留空，Publish directory 填 `.`（因为 `index.html` 在仓库根）
3. 或直接使用仓库根的 `render.yaml` Blueprint 一键部署
4. 部署完成后得到 `https://<名称>.onrender.com`

（仓库自带的 `Dockerfile` 在 Static Site 模式下不会被使用，可忽略。）

---

## 方式三：Railway（关联 GitHub → Docker）

前提：同样先 push 到 GitHub。

1. Railway 控制台 → New Project → **Deploy from GitHub repo**
2. Railway 会读取仓库根的 `railway.json` + `Dockerfile`，用官方 nginx 镜像托管静态文件
3. 部署完成后得到 `https://<随机名>.up.railway.app`

（`Dockerfile` 把 `index.html` + `assets/` 拷进 nginx 镜像，`nginx.conf.template` 监听平台注入的 `$PORT`。）

---

## 本地用 Docker 预览（可选）

```bash
docker build -t sop-admin .
docker run -e PORT=8080 -p 8080:8080 sop-admin
# 浏览器打开 http://localhost:8080
```

---

## 关于 Python 后端 `python_backend/`

它在「接真实平台 API」时才需要，本质是一个**常驻的监控 / 上架工作进程**，不是网页服务器，
因此**不随前端一起部署**。应单独跑在你自己的服务器 / 定时任务 / 容器里，由它调用亚马逊 SP-API
与拼多多开放平台，并把告警推送到企业微信 / 钉钉。前端「部署指南」页有完整步骤。
