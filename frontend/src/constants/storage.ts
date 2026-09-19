/**
 * 本地存储的键名集中定义
 *
 * 原来 'tripPlan' 这个字符串散在 Home.vue 和 Result.vue 两处，
 * 改名要全局搜、还容易漏。收在这里之后，
 * 组件只通过 usePlanCache() 访问，不直接碰 localStorage/sessionStorage。
 *
 * 用 sessionStorage 而不是 localStorage 是既有决策：
 * 行程数据可能很大（几十 KB），且只在当前标签页有意义；
 * 真正的持久化在后端 plan_store（SQLite）里，这里只是「跳转时不用等接口」的缓存。
 */

export const STORAGE_KEYS = {
  /** 刚生成的行程。Home 写入，Result 读取 */
  TRIP_PLAN: 'tripPlan',
  /**
   * 创建行程的草稿。首页「开始规划」写入，Create 页读取。
   *
   * 为什么需要它：首页只让用户写一句描述 + 挑城市，详细的日期与偏好
   * 挪到了独立的创建页。这两页之间要传递"用户刚才说了什么"。
   * 放 sessionStorage 而不是 URL query —— 描述可能很长，塞进地址栏既难看又有长度限制。
   */
  TRIP_DRAFT: 'tripDraft',
  /**
   * 登录令牌（后端 /api/auth/login 返回的 Bearer token）。api.ts 读写。
   *
   * ⚠️ **从 sessionStorage 改成 localStorage 了** —— 这是本次改动里
   * 唯一一处**主动放松**的存储策略，理由要说清楚：
   *
   *   旧的东西是一个**共享管理口令**。它不区分用户，关掉标签页就等于
   *   "登出"，而"关掉浏览器后写权限自动消失"在那个设计下是纯收益 ——
   *   共享电脑上少一个把写权限留给下一个人的风险。
   *
   *   现在是一个**按人签发的会话**。每次开新标签页都要重新输账号密码，
   *   是任何有账号体系的网站都不会做的事 —— 用户会认为"登录坏了"。
   *   而它的风险面也不同：泄露影响的是**一个人自己的数据**，
   *   不是"任何人都能清库"。后端还给了过期时间（默认 7 天）和
   *   "停用/改密立即吊销"两道兜底。
   *
   * 也就是说：这个改动**不是**因为 localStorage 变安全了，而是因为
   * 存的东西变了、代价也变了。在共享电脑上使用时仍然应当主动点「退出登录」——
   * 界面在账号菜单里提供了这个动作，而不是靠关标签页。
   */
  AUTH_TOKEN: 'tripmindAuthToken',
} as const;

export type StorageKey = (typeof STORAGE_KEYS)[keyof typeof STORAGE_KEYS];
