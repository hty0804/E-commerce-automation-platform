# 纯静态前端镜像：用官方 nginx 托管 index.html + assets/
# 适用于 Railway(Docker) / Hugging Face(Docker) / 任意容器平台
# 前端无需后端、无需构建 —— 直接把静态文件塞进 nginx 即可。
FROM nginx:1.27-alpine

# 容器平台会把服务端口注入到环境变量 PORT，给个默认值便于本地 docker run
ENV PORT=80

# nginx:alpine 的 entrypoint 会用 envsubst 处理 /etc/nginx/templates/*.template，
# 把 ${PORT} 替换成实际端口（Render / Railway / HF 都会注入 PORT）
COPY nginx.conf.template /etc/nginx/templates/default.conf.template

COPY index.html /usr/share/nginx/html/index.html
COPY assets /usr/share/nginx/html/assets

EXPOSE 80
