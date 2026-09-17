import html2canvas from 'html2canvas';
import jsPDF from 'jspdf';
import { message } from 'ant-design-vue';

/**
 * 行程导出（图片 / PDF）。
 *
 * 抽出来的直接原因：原来 `Result.vue` 里 exportAsImage 和 exportAsPDF
 * 有约 130 行**逐字重复**的代码 —— 都是「克隆 DOM → 替换地图 → 覆写卡片样式 →
 * html2canvas → 输出」，唯一区别是最后落地成 PNG 还是塞进 jsPDF。
 * 那种结构下改一处必然漏另一处。
 *
 * 现在两个入口共用同一套「采集 + 预处理」，只有输出那一步不同。
 */

/** 截图根节点。用 class 而不是 id 选择器，方便将来给别的位置复用 */
const CAPTURE_SELECTOR = '.plan-capture-root';
const MAP_CONTAINER_ID = 'amap-container';
/** 导出视图里的地图占位。它和真实地图是**两个容器**，所以不能共用 id */
const EXPORT_MAP_SELECTOR = '[data-export-map]';

/** 把采集到的 canvas 变成 PNG dataURL */
function captureOptions() {
  return {
    backgroundColor: '#f7f8fa',
    scale: 2,
    logging: false,
    useCORS: true,
    allowTaint: true,
  } as const;
}

/**
 * 克隆内容并做导出前的样式归一。
 *
 * 之所以要克隆而不是直接截原 DOM：
 * 页面上有吸顶侧栏、悬浮按钮、`overflow: hidden` 的圆角容器，
 * 直接截图会把它们一起截进去，或者被裁掉一半。
 * 克隆到离屏容器里重排一次，结果稳定得多。
 */
function buildExportClone(source: HTMLElement): HTMLElement {
  const clone = document.createElement('div');
  clone.style.width = `${source.offsetWidth}px`;
  clone.style.backgroundColor = '#f7f8fa';
  clone.style.padding = '20px';
  clone.innerHTML = source.innerHTML;

  // 地图是 canvas，html2canvas 对它的支持不稳定 —— 直接抓现成的 dataURL 贴成 img。
  //
  // 这里按「有没有 canvas」来挑容器，而不是 `getElementById` 取第一个：
  // 导出视图（见 Result.vue 的 .capture-stage）里也有一个地图位，只是空壳。
  // 一旦哪天它被排到真实地图前面，取第一个就会抓到空壳、导出的地图变空白。
  let mapCanvas: HTMLCanvasElement | null = null;
  document.querySelectorAll(`#${MAP_CONTAINER_ID}`).forEach((c) => {
    if (mapCanvas) return;
    mapCanvas = c.querySelector('canvas') as HTMLCanvasElement | null;
  });

  if (mapCanvas) {
    try {
      const snapshot = (mapCanvas as HTMLCanvasElement).toDataURL('image/png');
      const target =
        clone.querySelector(EXPORT_MAP_SELECTOR) ?? clone.querySelector(`#${MAP_CONTAINER_ID}`);
      if (target) {
        target.innerHTML = `<img src="${snapshot}" style="width:100%;height:100%;object-fit:cover;" />`;
      }
    } catch (e) {
      // 高德的地图 canvas 可能被跨域污染，toDataURL 会抛 SecurityError。
      // 这时保留原容器，截出来是一片空白总好过整个导出失败。
      console.warn('地图截图失败，导出内容中地图将为空白:', e);
    }
  }

  // 把 antd 的卡片类名剥掉，换成内联样式。
  // 原因：导出容器在文档流之外，antd 的 CSS 变量与层叠上下文未必生效，
  // 表现是导出的图片里卡片没有边框和阴影、头部文字变成黑色底白字。
  clone.querySelectorAll('.ant-card').forEach((el) => {
    const node = el as HTMLElement;
    node.className = '';
    node.style.cssText +=
      'background-color:#ffffff;border-radius:12px;box-shadow:0 4px 12px rgba(0,0,0,0.08);' +
      'margin-bottom:20px;overflow:hidden;';
  });

  clone.querySelectorAll('.ant-card-head').forEach((el) => {
    (el as HTMLElement).style.cssText +=
      'background-color:#667eea;color:#ffffff;padding:16px 24px;font-size:18px;font-weight:600;';
  });

  clone.querySelectorAll('.ant-card-body').forEach((el) => {
    (el as HTMLElement).style.cssText += 'background-color:#ffffff;padding:24px;';
  });

  clone.querySelectorAll('.ant-collapse-item').forEach((el) => {
    (el as HTMLElement).style.cssText +=
      'border:1px solid #e8e8e8;border-radius:12px;margin-bottom:12px;overflow:hidden;';
  });

  clone.querySelectorAll('.ant-affix, .ant-back-top').forEach((el) => {
    (el as HTMLElement).style.display = 'none';
  });

  return clone;
}

async function capture(source: HTMLElement): Promise<HTMLCanvasElement> {
  const clone = buildExportClone(source);

  // 放到视口外而不是 display:none —— 后者会让 html2canvas 量不到尺寸，
  // 截出来是 0×0 的空白图。
  clone.style.position = 'absolute';
  clone.style.left = '-99999px';
  clone.style.top = '0';
  document.body.appendChild(clone);

  try {
    return await html2canvas(clone, captureOptions());
  } finally {
    document.body.removeChild(clone);
  }
}

function downloadDataUrl(dataUrl: string, filename: string) {
  const link = document.createElement('a');
  link.download = filename;
  link.href = dataUrl;
  link.click();
}

/** 文件名里的城市名要去掉路径分隔符等非法字符 */
function safeName(city: string): string {
  return (city || '行程').replace(/[\\/:*?"<>|]/g, '_');
}

export function usePlanExport() {
  function findRoot(): HTMLElement | null {
    return document.querySelector(CAPTURE_SELECTOR) as HTMLElement | null;
  }

  async function exportImage(city: string): Promise<void> {
    const root = findRoot();
    if (!root) {
      message.error('页面上没有可导出的内容');
      return;
    }

    message.loading({ content: '正在生成图片…', key: 'export', duration: 0 });
    try {
      const canvas = await capture(root);
      downloadDataUrl(canvas.toDataURL('image/png'), `旅行计划_${safeName(city)}_${Date.now()}.png`);
      message.success({ content: '图片导出成功', key: 'export' });
    } catch (e) {
      console.error('导出图片失败:', e);
      message.error({ content: `导出图片失败：${(e as Error).message}`, key: 'export' });
    }
  }

  async function exportPdf(city: string): Promise<void> {
    const root = findRoot();
    if (!root) {
      message.error('页面上没有可导出的内容');
      return;
    }

    message.loading({ content: '正在生成 PDF…', key: 'export', duration: 0 });
    try {
      const canvas = await capture(root);
      const imgData = canvas.toDataURL('image/png');

      const pdf = new jsPDF({ orientation: 'portrait', unit: 'mm', format: 'a4' });

      const pageWidth = 210;
      const pageHeight = 297;
      const imgWidth = pageWidth;
      const imgHeight = (canvas.height * imgWidth) / canvas.width;

      // 长图分页：整张图按 A4 高度切片，每页重新放置同一个 img，
      // 靠 position 的负偏移控制显示哪一段。
      let heightLeft = imgHeight;
      let position = 0;

      pdf.addImage(imgData, 'PNG', 0, position, imgWidth, imgHeight);
      heightLeft -= pageHeight;

      while (heightLeft > 0) {
        position = heightLeft - imgHeight;
        pdf.addPage();
        pdf.addImage(imgData, 'PNG', 0, position, imgWidth, imgHeight);
        heightLeft -= pageHeight;
      }

      pdf.save(`旅行计划_${safeName(city)}_${Date.now()}.pdf`);
      message.success({ content: 'PDF 导出成功', key: 'export' });
    } catch (e) {
      console.error('导出 PDF 失败:', e);
      message.error({ content: `导出 PDF 失败：${(e as Error).message}`, key: 'export' });
    }
  }

  return { exportImage, exportPdf };
}
