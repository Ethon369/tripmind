/**
 * 前端配置的读取入口 —— 运行时注入优先,构建期变量兜底。
 *
 * ## 为什么要有这一层
 *
 * `import.meta.env.VITE_*` 是 **Vite 在构建时**替换成字面量的 ——
 * 也就是说它的值被**焊死在产物里**了。对 Docker 部署来说这很别扭:
 * 换一个高德 Key 就得重新构建镜像(装依赖 + 打包,几分钟)。
 * 别人第一次跑这个项目、发现地图空白时,几乎不可能想到"要重新 build"。
 *
 * 所以改成**运行时注入**:
 * - 容器启动时,nginx 的入口脚本按环境变量生成 `/config.js`
 * - 这个文件在业务代码之前加载(见 index.html),把值挂到
 *   `window.__TRIPMIND_CONFIG__`
 * - 这里优先读它,读不到再回落到构建期变量
 *
 * ## 为什么开发环境不受影响
 *
 * `npm run dev` 时服务的是 Vite,不经过 nginx,`/config.js` 不存在 →
 * `window.__TRIPMIND_CONFIG__` 是 undefined → 自动回落到 `.env` 里的
 * `VITE_AMAP_*`。本地开发流程一点没变。
 *
 * ## 安全性
 *
 * 高德 **Web 端(JS API)** Key 本来就是给浏览器用的、必须公开,
 * 靠高德控制台的**域名白名单**保护。所以放在运行时配置里没有任何额外风险。
 * ⚠️ 但这也意味着:`VITE_*` / `config.js` **绝不能**放需要保密的密钥
 * (后端的高德 Web 服务 Key、LLM Key 都不行 —— 它们只属于后端容器)。
 */

interface RuntimeConfig {
  /** 高德 Web 端(JS API)Key */
  amapJsKey?: string
  /** JS API 安全密钥(2021-12 之后申请的 Key 必须配) */
  amapSecurityCode?: string
}

declare global {
  interface Window {
    __TRIPMIND_CONFIG__?: RuntimeConfig
  }
}

function runtime(): RuntimeConfig {
  return window.__TRIPMIND_CONFIG__ ?? {}
}

/** 高德 Web 端(JS API)Key。空串表示没配 —— 地图会加载失败。 */
export function amapJsKey(): string {
  return runtime().amapJsKey || import.meta.env.VITE_AMAP_WEB_JS_KEY || ''
}

/** 高德 JS API 安全密钥。为空是合法的(老 Key 不需要)。 */
export function amapSecurityCode(): string {
  return runtime().amapSecurityCode || import.meta.env.VITE_AMAP_SECURITY_CODE || ''
}
