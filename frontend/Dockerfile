# 途灵 TripMind —— 前端镜像
#
# 两阶段:node 构建 → nginx 托管静态产物。
# 最终镜像里没有 node_modules、没有源码、没有 npm —— 只有 dist + nginx。

# ===========================================================================
# 一、构建阶段
# ===========================================================================
FROM node:20-alpine AS build

WORKDIR /app

# 国内构建默认走 npmmirror;国外可以用 --build-arg 覆盖
ARG NPM_REGISTRY=https://registry.npmmirror.com

# 先只拷清单,让 npm ci 这层能被缓存
COPY package.json package-lock.json ./
RUN npm ci --registry="${NPM_REGISTRY}"

COPY . .

# ⚠️ `npm run build` 会先跑 vue-tsc 做**类型检查**(tsconfig 开了 noUnusedLocals)。
#    类型错误会让构建失败 —— 这是有意的,别改成 `vite build` 绕过:
#    绕过的代价是类型错误只在运行时暴露。
#
# 这里**不需要** VITE_AMAP_WEB_JS_KEY:前端配置改成运行时注入了
# (见 src/config/runtime.ts + docker-entrypoint.d/30-runtime-config.sh),
# 所以换 Key 不用重新构建这个镜像。
RUN npm run build

# ===========================================================================
# 二、运行阶段
# ===========================================================================
FROM nginx:1.27-alpine

# 站点配置:SPA 回退 + /api 反代 + 长耗时请求的超时(见文件内说明)
COPY nginx.conf /etc/nginx/conf.d/default.conf

# 静态产物
COPY --from=build /app/dist /usr/share/nginx/html

# 运行时配置的生成脚本。nginx 官方镜像的入口会执行 /docker-entrypoint.d/*.sh,
# 顺序按文件名字母序 —— 30- 在内置的 20-envsubst 之后,不会打架。
COPY docker-entrypoint.d/30-runtime-config.sh /docker-entrypoint.d/30-runtime-config.sh
# COPY 在 Windows 上不保证带上可执行位,显式补一次
RUN chmod +x /docker-entrypoint.d/30-runtime-config.sh

EXPOSE 80

# 用 127.0.0.1 而不是 localhost:容器里 localhost 可能先解析到 IPv6,
# 而 nginx 默认只监听 IPv4 的 80。
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -q -O /dev/null http://127.0.0.1/ || exit 1
