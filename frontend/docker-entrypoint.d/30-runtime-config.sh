#!/bin/sh
# 生成前端的运行时配置 /usr/share/nginx/html/config.js
#
# nginx 官方镜像会按**字母序**执行 /docker-entrypoint.d/*.sh,
# 所以 30- 排在内置的 20-envsubst-on-templates.sh 之后。
#
# 为什么不用 envsubst:那要多装一个 gettext、多一个模板文件,
# 而这里只需要拼两行 JS。用 heredoc 一步到位,依赖更少。
#
# 生成的只是**公开**的前端配置(高德 JS API Key 靠域名白名单保护),
# 后端的高德 Web 服务 Key / LLM Key 绝不能出现在这里。

set -eu

target=/usr/share/nginx/html/config.js

# 值里若出现反斜杠、双引号或 `</`,直接拼进 JS 字符串会把文件写坏
# (高德 Key 实际只含字母数字和短横线,但"实际不会"不等于"不会")。
escape() {
    printf '%s' "${1:-}" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' -e 's|</|\\u003c/|g'
}

cat > "$target" <<EOF
// 由容器启动脚本生成 —— 改环境变量后**重启容器**即可,不需要重新构建镜像。
// 不要手改:重启就没了。
window.__TRIPMIND_CONFIG__ = {
  amapJsKey: "$(escape "${VITE_AMAP_WEB_JS_KEY:-}")",
  amapSecurityCode: "$(escape "${VITE_AMAP_SECURITY_CODE:-}")",
};
EOF

# 只报"配没配",不打印值 —— 日志里不该出现凭据,哪怕它是公开的
if [ -n "${VITE_AMAP_WEB_JS_KEY:-}" ]; then
    echo "runtime-config: 已注入高德 JS API Key"
else
    echo "runtime-config: 未设置 VITE_AMAP_WEB_JS_KEY —— 结果页的地图会加载失败(其余功能正常)"
fi
