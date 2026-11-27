export interface AnalyticsConfig {
  gaMeasurementId: string;
  cfAnalyticsToken: string;
  debug: boolean;
  gaEnabled: boolean;
  cfEnabled: boolean;
}

const STATIC_ANALYTICS_ENV: Record<string, unknown> = {
  PUBLIC_GA_MEASUREMENT_ID: import.meta.env.PUBLIC_GA_MEASUREMENT_ID,
  PUBLIC_CF_ANALYTICS_TOKEN: import.meta.env.PUBLIC_CF_ANALYTICS_TOKEN,
  PUBLIC_ANALYTICS_DEBUG: import.meta.env.PUBLIC_ANALYTICS_DEBUG,
};

function readEnvStr(key: string): string {
  const fromProcess =
    typeof process !== 'undefined' && process.env ? process.env[key] : undefined;
  if (typeof fromProcess === 'string' && fromProcess.trim()) {
    return fromProcess.trim();
  }
  const fromImportMeta = STATIC_ANALYTICS_ENV[key];
  return typeof fromImportMeta === 'string' ? fromImportMeta.trim() : '';
}

function readBoolEnv(key: string, defaultValue = false): boolean {
  const raw = readEnvStr(key).toLowerCase();
  if (!raw) return defaultValue;
  return raw === '1' || raw === 'true' || raw === 'yes' || raw === 'on';
}

export function isValidGaMeasurementId(value: string): boolean {
  return /^G-[A-Z0-9]{6,20}$/i.test(value.trim());
}

export function isValidCfAnalyticsToken(value: string): boolean {
  return /^[a-f0-9]{16,64}$/i.test(value.trim());
}

export function getAnalyticsConfig(): AnalyticsConfig {
  const gaMeasurementId = readEnvStr('PUBLIC_GA_MEASUREMENT_ID');
  const cfAnalyticsToken = readEnvStr('PUBLIC_CF_ANALYTICS_TOKEN');
  const debug = readBoolEnv('PUBLIC_ANALYTICS_DEBUG', false);

  return {
    gaMeasurementId,
    cfAnalyticsToken,
    debug,
    gaEnabled: isValidGaMeasurementId(gaMeasurementId),
    cfEnabled: isValidCfAnalyticsToken(cfAnalyticsToken),
  };
}
