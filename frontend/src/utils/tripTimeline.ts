import type { Attraction, DayPlan, Meal } from '@/types';

/**
 * 把一天的景点串成带时间的时间轴。
 *
 * 参考产品的时间轴是这样的：
 *   09:00  故宫博物院 · 3h
 *          ↓ 1.0km · 13分钟
 *   12:13  四季民福烤鸭店 · 1.5h
 *
 * 这里负责算出那两个数字：**每段交通的距离与耗时**。
 *
 * ⚠️ 关于精度，必须说清楚：
 * 耗时是**直线距离估算**，不是真实路线规划。
 * 真实路线要调高德的 `maps_direction_*`（后端有 `/api/map/route`），
 * 但一天有 3–4 段、三天就十几次调用 —— 为了给时间轴填两个数字付这个代价不值得。
 * 估算的误差主要来自"直线 ≠ 实际路网"，所以界面上标注为「预估」。
 */

/** 每天从几点开始排。9 点是个折中：不算太早，也给一天留够时间 */
const DAY_START_HOUR = 9;

/** 步行速度（米/分钟）。1.2 m/s 是常见成人步速 */
const WALK_M_PER_MIN = 80;

/** 打车时折算的平均速度（米/分钟）。含等车与红绿灯 */
const TAXI_M_PER_MIN = 400;

/** 超过这个距离就不建议走路了 */
const WALK_LIMIT_M = 1200;

/** 打车固定附加时间（等车 + 上下车），分钟 */
const TAXI_OVERHEAD_MIN = 5;

export interface TimelineLeg {
  /** 直线距离（米，已取整） */
  meters: number;
  /** 预估耗时（分钟） */
  minutes: number;
  /** '步行' | '打车' */
  mode: string;
}

export interface TimelineStop {
  /** 'HH:mm' */
  time: string;
  attraction: Attraction;
  /** 从上个点走到这里的交通。第一个点没有 */
  leg?: TimelineLeg;
  /** 在本点的停留时长（分钟），便于展示 */
  stayMinutes: number;
}

/** 两点间的直线距离（米），haversine 公式 */
export function distanceMeters(
  a: { longitude: number; latitude: number },
  b: { longitude: number; latitude: number }
): number {
  const R = 6371000;
  const toRad = (d: number) => (d * Math.PI) / 180;

  const dLat = toRad(b.latitude - a.latitude);
  const dLon = toRad(b.longitude - a.longitude);
  const lat1 = toRad(a.latitude);
  const lat2 = toRad(b.latitude);

  const h =
    Math.sin(dLat / 2) ** 2 + Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(h));
}

/**
 * 估算两点之间的交通方式与耗时。
 *
 * 近的走路、远的打车 —— 这也是真实行程里最常见的两段式。
 * 不做"公交/地铁"估算：那需要真实路网与线路数据，直线距离推不出来。
 */
export function estimateLeg(
  a: { longitude: number; latitude: number },
  b: { longitude: number; latitude: number }
): TimelineLeg {
  const meters = distanceMeters(a, b);

  if (meters <= WALK_LIMIT_M) {
    return {
      meters: Math.round(meters),
      minutes: Math.max(1, Math.round(meters / WALK_M_PER_MIN)),
      mode: '步行',
    };
  }

  return {
    meters: Math.round(meters),
    minutes: Math.round(meters / TAXI_M_PER_MIN) + TAXI_OVERHEAD_MIN,
    mode: '打车',
  };
}

const pad2 = (n: number) => String(n).padStart(2, '0');

const toClock = (minutesFromMidnight: number) => {
  const h = Math.floor(minutesFromMidnight / 60) % 24;
  const m = minutesFromMidnight % 60;
  return `${pad2(h)}:${pad2(m)}`;
};

/**
 * 把一天的景点排成时间轴。
 *
 * 有坐标的景点才能算交通时间；没坐标的会退化成"直接接上下一个"，
 * 不会因为缺数据就把整条时间轴算崩。
 */
export function buildDayTimeline(day: DayPlan): TimelineStop[] {
  const attractions = day.attractions || [];
  if (!attractions.length) return [];

  const stops: TimelineStop[] = [];
  let clock = DAY_START_HOUR * 60;

  attractions.forEach((a, i) => {
    const stay = a.visit_duration || 60;

    if (i === 0) {
      stops.push({ time: toClock(clock), attraction: a, stayMinutes: stay });
      clock += stay;
      return;
    }

    const prev = attractions[i - 1];
    let leg: TimelineLeg | undefined;

    if (prev.location?.longitude && a.location?.longitude) {
      leg = estimateLeg(prev.location, a.location);
      clock += leg.minutes;
    }

    stops.push({ time: toClock(clock), attraction: a, leg, stayMinutes: stay });
    clock += stay;
  });

  return stops;
}

/** 一天的汇总数字：景点数、总距离、总游览时长 */
export function summarizeDay(day: DayPlan) {
  const attractions = day.attractions || [];
  const stops = buildDayTimeline(day);

  const travelMeters = stops.reduce((sum, s) => sum + (s.leg?.meters ?? 0), 0);
  const stayMinutes = attractions.reduce((sum, a) => sum + (a.visit_duration || 0), 0);
  const legMinutes = stops.reduce((sum, s) => sum + (s.leg?.minutes ?? 0), 0);

  return {
    attractions: attractions.length,
    travelMeters,
    travelMinutes: legMinutes,
    /** 全天在外的时长：游览 + 交通 */
    totalMinutes: stayMinutes + legMinutes,
    mealCount: (day.meals || []).length,
  };
}

/** 把米格式化成"1.2 km" / "800 m" */
export function formatDistance(meters: number): string {
  if (meters < 1000) return `${meters} m`;
  return `${(meters / 1000).toFixed(1)} km`;
}

/** 把分钟格式化成"3h" / "1.5h" / "45分钟" */
export function formatDuration(minutes: number): string {
  if (minutes < 60) return `${minutes} 分钟`;
  const h = minutes / 60;
  return Number.isInteger(h) ? `${h}h` : `${h.toFixed(1)}h`;
}

/** 餐饮时段的中文名 */
export const MEAL_ORDER: Array<Meal['type']> = ['breakfast', 'lunch', 'dinner', 'snack'];
