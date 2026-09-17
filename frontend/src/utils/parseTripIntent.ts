/**
 * 从用户的一句话里解析出结构化的行程意图。
 *
 * 为什么需要它：首页只让用户写一句「我想去重庆玩五天」，如果创建页把这句话
 * 原样丢进「补充说明」、却让目的地和天数**空着**，用户就得把自己刚说过的话再填一遍 ——
 * 这是最劝退的一类重复劳动。
 *
 * 这里做的是**保守的预填**，不是"理解"：
 *   - 解析出来了 → 预填进表单，用户在创建页能改
 *   - 解析不出来 → 保持为空，用户手填（与原来一致，不会更差）
 * 所以宁可漏，不可错 —— 一个字面量解析错误比"没解析出来"更让人困惑。
 *
 * ⚠️ 局限（写清楚，避免以后误以为它很聪明）
 *   1. 纯规则匹配，不理解语义。「我想去云南，顺便去趟重庆」只会取到第一个地名。
 *   2. 依赖中文表达习惯，「玩五天」「待 5 天」能认，「5d」「五天左右」认不出。
 *   3. 不做城市合法性校验 —— 用户说「去北欧」，那就填「北欧」，交给后端处理。
 *   想真正做到"理解"，得让后端用 LLM 解析（一次调用的成本很低，但那是另一件事）。
 */

/** 中文数字 → 阿拉伯数字。只覆盖 1–99 的常见写法 */
const CN_DIGITS: Record<string, number> = {
  零: 0,
  一: 1,
  二: 2,
  两: 2,
  三: 3,
  四: 4,
  五: 5,
  六: 6,
  七: 7,
  八: 8,
  九: 9,
};

/**
 * 解析中文数字字符串。支持：`五` / `十` / `十五` / `二十` / `二十八` / 纯阿拉伯数字。
 * 超出 99 或写法不认识时返回 null。
 */
function parseNumber(raw: string): number | null {
  const s = raw.trim();
  if (!s) return null;

  // 阿拉伯数字直接返回
  if (/^\d{1,3}$/.test(s)) return parseInt(s, 10);

  // 只有「十」= 10
  if (s === '十') return 10;

  // 「十X」= 1X
  if (s.length === 2 && s[0] === '十') {
    const ones = CN_DIGITS[s[1]];
    return ones === undefined ? null : 10 + ones;
  }

  const tenIdx = s.indexOf('十');
  if (tenIdx > 0) {
    const tens = CN_DIGITS[s[0]];
    if (tens === undefined) return null;
    // 「X十」= X0
    if (s.length === 2) return tens * 10;
    // 「X十Y」= XY
    const ones = CN_DIGITS[s[2]];
    return ones === undefined ? null : tens * 10 + ones;
  }

  // 单个中文数字
  const single = CN_DIGITS[s];
  return single === undefined ? null : single;
}

export interface ParsedIntent {
  /** 目的地。可能是市、省或地区 —— 不做合法性校验 */
  city?: string;
  /** 天数（1–30） */
  days?: number;
}

/**
 * 从一句话里解析目的地与天数。
 *
 * 能认出的表达：
 *   我想去重庆玩五天            → 重庆 / 5
 *   国庆想去云南待 7 天          → 云南 / 7
 *   打算去北京旅游，大概三天      → 北京 / 3
 *   去成都                → 成都
 *   想去西安看看，玩十天         → 西安 / 10
 */
export function parseTripIntent(text: string): ParsedIntent {
  const out: ParsedIntent = {};
  const s = (text || '').trim();
  if (!s) return out;

  /* ---------------- 天数 ----------------
   * 「五天」「5 天」「10日」都能认。
   * 限定 1–30 是因为后端 travel_days 的约束就是 ge=1 le=30 ——
   * 解析出一个 60 反而会被后端打回，不如不填。 */
  const dayMatch = s.match(/(\d{1,2}|[一二两三四五六七八九十]{1,3})\s*[天日]/);
  if (dayMatch) {
    const n = parseNumber(dayMatch[1]);
    if (n !== null && n >= 1 && n <= 30) out.days = n;
  }

  /* ---------------- 目的地 ----------------
   * 用「去/到 + 地名 + 动词/标点/结尾」这个结构来定位。
   * 地名限定 2–5 个汉字，且用**非贪婪**匹配 —— 否则「去重庆玩五天」
   * 会贪婪地把「重庆玩五天」整个当地名。 */
  const CITY_PATTERNS = [
    // 去重庆玩 / 到成都看看 / 去云南待几天
    /(?:想去|要去|去|到)\s*([\u4e00-\u9fa5]{2,5}?)(?=玩|旅游|旅行|游|逛|待|呆|吃|看|耍|溜达|度假|出差|，|,|。|！|!|、|\s|$)/,
  ];

  for (const re of CITY_PATTERNS) {
    const m = s.match(re);
    if (m?.[1]) {
      const city = m[1].trim();
      // 兜底：误把「五天」「三天」当地名时丢弃（虽然正则已尽量避开）
      if (city && !/^[一二两三四五六七八九十]+[天日]$/.test(city)) {
        out.city = city;
        break;
      }
    }
  }

  return out;
}
