import { describe, expect, it } from 'vitest';
import { resolveLocale, translate } from './i18n';

describe('resolveLocale', () => {
  it('uses Chinese when the browser language is Chinese', () => {
    expect(resolveLocale(['zh-CN'])).toBe('zh-CN');
    expect(resolveLocale(['zh-Hans-CN'])).toBe('zh-CN');
    expect(resolveLocale(null, 'zh-TW')).toBe('zh-CN');
  });

  it('uses English for English browsers and unsupported languages', () => {
    expect(resolveLocale(['en-US'])).toBe('en');
    expect(resolveLocale(['fr-FR'])).toBe('en');
    expect(resolveLocale()).toBe('en');
  });

  it('walks browser language preferences until a supported locale matches', () => {
    expect(resolveLocale(['fr-FR', 'zh-CN', 'en-US'])).toBe('zh-CN');
    expect(resolveLocale(['fr-FR', 'en-US', 'zh-CN'])).toBe('en');
  });
});

describe('translate', () => {
  it('interpolates values for the active locale', () => {
    expect(translate('zh-CN', 'api.usingDemoData', { error: 'offline' })).toBe('正在使用演示数据：offline');
    expect(translate('en', 'instances.selected', { count: 2 })).toBe('2 instances selected');
  });
});
