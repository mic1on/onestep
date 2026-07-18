import { afterEach, describe, expect, it, vi } from 'vitest';
import { readStoredLocale, resolveLocale, translate, writeStoredLocale } from './i18n';

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

  // Regression: the sink panel title used to be "{sink} Analytics Engine",
  // which fabricated an analytics role for every sink (a memory_queue was
  // badged as an "analysis engine"). Keep it neutral so any sink kind fits.
  it('uses a neutral sink title that fits any sink kind', () => {
    expect(translate('en', 'topology.sinkTitle', { sink: 'demo.results' })).toBe('demo.results Sink');
    expect(translate('zh-CN', 'topology.sinkTitle', { sink: 'demo.results' })).toBe('demo.results Sink');
  });
});

describe('locale persistence', () => {
  // jsdom provides a working localStorage; clear between tests so each is isolated.
  afterEach(() => {
    window.localStorage.clear();
    vi.restoreAllMocks();
  });

  it('returns the stored locale when a valid value is persisted', () => {
    window.localStorage.setItem('onestep.controlPlane.locale', 'zh-CN');
    expect(readStoredLocale()).toBe('zh-CN');
  });

  it('returns null when nothing is stored so the provider falls back to browser detection', () => {
    expect(readStoredLocale()).toBeNull();
  });

  it('returns null for an unrecognized stored value instead of casting it', () => {
    window.localStorage.setItem('onestep.controlPlane.locale', 'klingon');
    expect(readStoredLocale()).toBeNull();
  });

  it('persists the chosen locale to localStorage', () => {
    writeStoredLocale('zh-CN');
    expect(window.localStorage.getItem('onestep.controlPlane.locale')).toBe('zh-CN');
  });

  it('does not throw when localStorage reads fail (e.g. private mode)', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    expect(readStoredLocale()).toBeNull();
  });

  it('does not throw when localStorage writes fail (e.g. private mode)', () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    expect(() => writeStoredLocale('en')).not.toThrow();
  });
});
