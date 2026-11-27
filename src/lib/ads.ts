export type AdMode = 'off' | 'preview' | 'adsense';

export type AdSlotKey = 'home-feed' | 'item-calc' | 'item-bottom' | 'footer';

export interface AdSlotMeta {
  key: AdSlotKey;
  label: string;
  recommendedSize: string;
  adFormat: 'auto' | 'fluid' | 'rectangle' | 'horizontal';
  minHeightPx: number;
  envVarName: string;
}

export const AD_SLOT_CATALOG: Record<AdSlotKey, AdSlotMeta> = {
  'home-feed': {
    key: 'home-feed',
    label: '首頁商品牆下方廣告位',
    recommendedSize: '響應式橫幅（建議 728×90 / 手機 320×100）',
    adFormat: 'auto',
    minHeightPx: 100,
    envVarName: 'PUBLIC_ADSENSE_SLOT_HOME_FEED',
  },
  'item-calc': {
    key: 'item-calc',
    label: '計算機下方高曝光廣告位',
    recommendedSize: '文內原生 / 矩形自適應（建議 336×280 或響應式）',
    adFormat: 'auto',
    minHeightPx: 120,
    envVarName: 'PUBLIC_ADSENSE_SLOT_ITEM_CALC',
  },
  'item-bottom': {
    key: 'item-bottom',
    label: '商品頁 FAQ 下方廣告位',
    recommendedSize: '響應式橫幅 / Multiplex 多重內容廣告',
    adFormat: 'auto',
    minHeightPx: 110,
    envVarName: 'PUBLIC_ADSENSE_SLOT_ITEM_BOTTOM',
  },
  footer: {
    key: 'footer',
    label: '全站頁尾上方橫幅廣告位',
    recommendedSize: '全站響應式橫幅（建議 728×90 / 手機 320×50）',
    adFormat: 'horizontal',
    minHeightPx: 90,
    envVarName: 'PUBLIC_ADSENSE_SLOT_FOOTER',
  },
};

export interface AdsGlobalConfig {
  enabled: boolean;
  mode: AdMode;
  adsenseClient: string;
  slotIds: Record<AdSlotKey, string>;
}

function parseBoolEnv(raw: unknown, defaultValue = false): boolean {
  if (typeof raw === 'boolean') return raw;
  const normalized = String(raw ?? '').trim().toLowerCase();
  if (!normalized) return defaultValue;
  return normalized === '1' || normalized === 'true' || normalized === 'yes' || normalized === 'on';
}

const STATIC_PUBLIC_ENV: Record<string, unknown> = {
  PUBLIC_ADS_ENABLED: import.meta.env.PUBLIC_ADS_ENABLED,
  PUBLIC_ADS_MODE: import.meta.env.PUBLIC_ADS_MODE,
  PUBLIC_ADSENSE_CLIENT: import.meta.env.PUBLIC_ADSENSE_CLIENT,
  PUBLIC_ADSENSE_SLOT_HOME_FEED: import.meta.env.PUBLIC_ADSENSE_SLOT_HOME_FEED,
  PUBLIC_ADSENSE_SLOT_ITEM_CALC: import.meta.env.PUBLIC_ADSENSE_SLOT_ITEM_CALC,
  PUBLIC_ADSENSE_SLOT_ITEM_BOTTOM: import.meta.env.PUBLIC_ADSENSE_SLOT_ITEM_BOTTOM,
  PUBLIC_ADSENSE_SLOT_FOOTER: import.meta.env.PUBLIC_ADSENSE_SLOT_FOOTER,
};

function readEnvStr(key: string): string {
  const fromProcess =
    typeof process !== 'undefined' && process.env ? process.env[key] : undefined;
  if (typeof fromProcess === 'string' && fromProcess.trim()) {
    return fromProcess.trim();
  }
  const fromImportMeta = STATIC_PUBLIC_ENV[key];
  return typeof fromImportMeta === 'string' ? fromImportMeta.trim() : '';
}

function readEnvRaw(key: string): unknown {
  const fromProcess =
    typeof process !== 'undefined' && process.env ? process.env[key] : undefined;
  if (fromProcess !== undefined && fromProcess !== '') {
    return fromProcess;
  }
  return STATIC_PUBLIC_ENV[key];
}

export function getAdsConfig(): AdsGlobalConfig {
  const enabled = parseBoolEnv(readEnvRaw('PUBLIC_ADS_ENABLED'), false);
  const rawMode = readEnvStr('PUBLIC_ADS_MODE').toLowerCase();
  const adsenseClient = readEnvStr('PUBLIC_ADSENSE_CLIENT');

  let mode: AdMode = 'off';
  if (enabled) {
    if (rawMode === 'preview') {
      mode = 'preview';
    } else if (rawMode === 'adsense' || (!rawMode && adsenseClient)) {
      mode = 'adsense';
    } else if (rawMode === 'off') {
      mode = 'off';
    } else {
      // 開啟 PUBLIC_ADS_ENABLED=true 但尚未填入 AdSense ID 時，預設顯示預覽占位框方便調版。
      mode = 'preview';
    }
  }

  return {
    enabled,
    mode,
    adsenseClient,
    slotIds: {
      'home-feed': readEnvStr('PUBLIC_ADSENSE_SLOT_HOME_FEED'),
      'item-calc': readEnvStr('PUBLIC_ADSENSE_SLOT_ITEM_CALC'),
      'item-bottom': readEnvStr('PUBLIC_ADSENSE_SLOT_ITEM_BOTTOM'),
      footer: readEnvStr('PUBLIC_ADSENSE_SLOT_FOOTER'),
    },
  };
}

export interface ResolvedAdSlot {
  renderMode: AdMode;
  meta: AdSlotMeta;
  adsenseClient: string;
  slotId: string;
}

export function resolveAdSlot(
  slotKey: AdSlotKey,
  overrideSlotId?: string,
): ResolvedAdSlot {
  const config = getAdsConfig();
  const meta = AD_SLOT_CATALOG[slotKey];
  const slotId = (overrideSlotId ?? config.slotIds[slotKey] ?? '').trim();

  if (!config.enabled || config.mode === 'off') {
    return { renderMode: 'off', meta, adsenseClient: config.adsenseClient, slotId };
  }

  if (config.mode === 'adsense') {
    if (!isValidAdsenseClient(config.adsenseClient) || !slotId) {
      return { renderMode: 'off', meta, adsenseClient: config.adsenseClient, slotId };
    }
    return { renderMode: 'adsense', meta, adsenseClient: config.adsenseClient, slotId };
  }

  return { renderMode: 'preview', meta, adsenseClient: config.adsenseClient, slotId };
}

export function isValidAdsenseClient(client: string): boolean {
  return /^ca-pub-\d{10,20}$/.test(client.trim());
}

/**
 * 轉換 ca-pub-XXXX 為 ads.txt 需要的 pub-XXXX 格式。
 */
export function formatAdsTxtBody(adsenseClient: string): string {
  const cleaned = adsenseClient.trim();
  if (!isValidAdsenseClient(cleaned)) {
    return '# 尚未設定有效的 PUBLIC_ADSENSE_CLIENT（格式：ca-pub-xxxxxxxxxxxxxxxx）\n';
  }
  const pubId = cleaned.replace(/^ca-/, '');
  return `google.com, ${pubId}, DIRECT, f08c47fec0942fa0\n`;
}
