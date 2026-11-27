import rawProducts from '../data/products.json';

export type Category =
  | '衛生紙'
  | '洗衣膠囊'
  | '濕紙巾'
  | '紙尿褲'
  | '清潔劑'
  | '其他日用品';

export const CATEGORIES: readonly Category[] = [
  '衛生紙',
  '洗衣膠囊',
  '濕紙巾',
  '紙尿褲',
  '清潔劑',
  '其他日用品',
];

export interface Product {
  slug: string;
  category: Category;
  brand: string;
  name: string;
  unit_name: string;
  pack_count: number;
  units_per_pack: number;
  total_units: number;
  regular_price: number;
  coupang_price: number;
  god_tier_unit_price: number;
  coupang_affiliate_url: string;
  features: string[];
  best_for: string;
  editor_note?: string;
  image_url?: string;
  updated_at: string;
}

export interface ProductWithMetrics extends Product {
  unitPrice: number;
  regularUnitPrice: number;
  savingsAmount: number;
  savingsPercent: number;
  isGodTier: boolean;
  godTierDiff: number;
}

export function computeProductMetrics(product: Product): ProductWithMetrics {
  const unitPrice = product.coupang_price / product.total_units;
  const regularUnitPrice = product.regular_price / product.total_units;
  const savingsAmount = Math.max(0, product.regular_price - product.coupang_price);
  const savingsPercent =
    product.regular_price > 0
      ? ((product.regular_price - product.coupang_price) / product.regular_price) * 100
      : 0;
  const isGodTier = unitPrice <= product.god_tier_unit_price;
  const godTierDiff = unitPrice - product.god_tier_unit_price;

  return {
    ...product,
    unitPrice,
    regularUnitPrice,
    savingsAmount,
    savingsPercent,
    isGodTier,
    godTierDiff,
  };
}

export function getAllProductsWithMetrics(): ProductWithMetrics[] {
  return (rawProducts as Product[]).map(computeProductMetrics);
}

/**
 * 依據單價級距自動選擇適合閱讀的小數位數（如毫升／克等極小單價顯示 4 位、衛生紙／濕紙巾顯示 3 位、洗衣膠囊／紙尿褲顯示 2 位）。
 */
export function formatUnitPrice(price: number): string {
  if (!Number.isFinite(price)) return '0.00';
  if (price > 0 && price < 0.1) return price.toFixed(4);
  return price < 1 ? price.toFixed(3) : price.toFixed(2);
}

export function formatCurrency(amount: number): string {
  if (!Number.isFinite(amount)) return '0';
  return Math.round(amount).toLocaleString('zh-TW');
}

export function formatPercent(percent: number): string {
  if (!Number.isFinite(percent)) return '0.0';
  return percent.toFixed(1);
}

/**
 * 安全序列化 JSON-LD，避免字串內角括號提早關閉 <script> 標籤。
 */
export function sanitizeJsonLd(data: unknown): string {
  return JSON.stringify(data)
    .replace(/</g, '\\u003c')
    .replace(/>/g, '\\u003e')
    .replace(/&/g, '\\u0026');
}

export interface ProductFaqItem {
  question: string;
  answer: string;
}

/**
 * 依商品分類與實算單價產生貼近台灣買家疑問的 FAQ（避免全站千篇一律的 AI 模板句型）。
 */
export function buildProductFaqs(product: ProductWithMetrics): ProductFaqItem[] {
  const unitPriceText = formatUnitPrice(product.unitPrice);
  const regularUnitPriceText = formatUnitPrice(product.regularUnitPrice);
  const godTierText = formatUnitPrice(product.god_tier_unit_price);
  const savingsPercentText = formatPercent(product.savingsPercent);
  const savingsAmountText = formatCurrency(product.savingsAmount);
  const totalUnitsText = formatCurrency(product.total_units);
  const coupangPriceText = formatCurrency(product.coupang_price);
  const regularPriceText = formatCurrency(product.regular_price);

  const packSummaryLine =
    product.pack_count > 1
      ? `這組拆開來是 ${product.pack_count} 組 × ${product.units_per_pack} ${product.unit_name}，總共 ${totalUnitsText} ${product.unit_name}。`
      : `這組單件規格是 ${totalUnitsText} ${product.unit_name}。`;

  const q1 = {
    question: `${product.brand} ${product.name} 平均一${product.unit_name}多少錢？現在買有省到嗎？`,
    answer:
      packSummaryLine +
      `以目前酷澎 NT$${coupangPriceText} 來算，平均每${product.unit_name}是 NT$${unitPriceText}；` +
      `對比一般賣場常見售價約 NT$${regularPriceText}（每${product.unit_name}約 NT$${regularUnitPriceText}），整組買下來大概少花 NT$${savingsAmountText}（約 ${savingsPercentText}%）。`,
  };

  const verdictLine = product.isGodTier
    ? `現在每${product.unit_name} NT$${unitPriceText} 已經踩進這個門檻裡，家裡剛好要補貨或放得下整箱的話，算是不必再等檔期的好價錢。`
    : `目前離門檻還差約 NT$${formatUnitPrice(product.godTierDiff)}/${product.unit_name}，如果不急著用，可以等賣場發券或先用上方計算機跟別家通路比看看。`;

  const q2 = {
    question: `${product.brand} 這款通常多少錢才算「神價」？現在適合囤整箱嗎？`,
    answer:
      `以台灣省錢版跟日常囤貨習慣來看，這款通常抓每${product.unit_name} NT$${godTierText} 以下算漂亮價位。` +
      verdictLine,
  };

  let q3: ProductFaqItem;
  if (product.category === '衛生紙') {
    q3 = {
      question: `這款衛生紙抽數跟紙質適合放房間還是廁所？箱購會不會佔空間？`,
      answer:
        `${product.editor_note ? product.editor_note + ' ' : ''}` +
        `買整箱（${product.pack_count} 包）雖然單${product.unit_name}比較省，但體積不小，小套房建議先確認衣櫃上方或床底放不放得下；另外它是「${product.best_for}」的常見選擇。`,
    };
  } else if (product.category === '洗衣膠囊') {
    q3 = {
      question: `洗衣膠囊算一顆 ${unitPriceText} 元，比買大桶洗衣精划算嗎？`,
      answer:
        `如果平常一次都洗一整籃衣服，一顆約 NT$${unitPriceText} 省去倒洗劑和洗量杯的麻煩其實很划算；但若常洗只有兩三件的小水量，單顆成本就會偏高。` +
        `${product.editor_note ? product.editor_note : `這款特別合拍的是「${product.best_for}」。`}`,
    };
  } else if (product.category === '濕紙巾') {
    q3 = {
      question: `箱購 ${product.pack_count} 包濕紙巾用不完會乾掉嗎？有蓋跟補充包差在哪？`,
      answer:
        `未開封放在陰涼處通常放一年以上沒問題，重點是開封後封口貼紙要壓緊或直接放進濕巾盒。` +
        `${product.editor_note ? product.editor_note : `這款比較適合「${product.best_for}」。`}`,
    };
  } else if (product.category === '紙尿褲') {
    q3 = {
      question: `這款紙尿褲一片 ${unitPriceText} 元，一次囤整箱怕寶寶長太快穿不下怎麼辦？`,
      answer:
        `寶寶正值換尺寸邊緣（例如接近體重上限）時，建議先買單包或一箱就好，別一次囤兩三箱以免後半箱勒腿漏尿。` +
        `${product.editor_note ? product.editor_note : `日常使用上適合「${product.best_for}」。`}`,
    };
  } else if (product.category === '清潔劑') {
    q3 = {
      question: `買大桶或補充包會不會太重難倒，或是放太久變質？`,
      answer:
        `家裡用量大直接買大桶或箱裝單價最壓得下來，但大瓶裝真的滿沉的，建議宅配送到家順便自備小壓頭或分裝瓶才好倒。` +
        `${product.editor_note ? product.editor_note : `日常使用上特別合拍「${product.best_for}」。`}`,
    };
  } else if (product.category === '其他日用品') {
    q3 = {
      question: `廚房紙巾或居家消耗品一次買這麼多組，收納跟消耗速度要注意什麼？`,
      answer:
        `廚房消耗品單價差很多，整箱帶雖然每張壓得很低，但拆袋後記得避開瓦斯爐邊濕氣跟油煙，才不會後面幾捲泛黃受潮。` +
        `${product.editor_note ? product.editor_note : `整體來說適合「${product.best_for}」。`}`,
    };
  } else {
    q3 = {
      question: `買這組之前有什麼要注意的地方？實際適合誰？`,
      answer:
        `${product.editor_note ? product.editor_note + ' ' : ''}` +
        `整體來看比較合拍「${product.best_for}」，下單前也建議對一下頁面顯示的實際組數與容量。`,
    };
  }

  return [q1, q2, q3];
}
