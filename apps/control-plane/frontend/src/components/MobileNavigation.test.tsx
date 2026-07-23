import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { I18nProvider } from '../i18n';
import MobileNavigation from './MobileNavigation';

function renderNavigation(onViewChange = vi.fn()) {
  render(
    <I18nProvider initialLocale="en">
      <MobileNavigation currentView="overview" onLogout={vi.fn()} onViewChange={onViewChange} />
    </I18nProvider>,
  );
  return onViewChange;
}

describe('MobileNavigation', () => {
  it('navigates to services from the bottom bar', async () => {
    const user = userEvent.setup();
    const onViewChange = renderNavigation();

    await user.click(screen.getByRole('button', { name: 'Services' }));

    expect(onViewChange).toHaveBeenCalledWith('servicesList');
  });

  it('closes More with Escape and restores trigger focus', async () => {
    const user = userEvent.setup();
    renderNavigation();
    const trigger = screen.getByRole('button', { name: 'Open more menu' });

    await user.click(trigger);
    expect(screen.getByRole('dialog', { name: 'More' })).toBeTruthy();
    await user.keyboard('{Escape}');

    expect(screen.queryByRole('dialog', { name: 'More' })).toBeNull();
    expect(document.activeElement).toBe(trigger);
  });
});
