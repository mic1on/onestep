import { fireEvent, render } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { I18nProvider } from '../i18n';
import LookbackControl from './LookbackControl';

describe('LookbackControl', () => {
  it('reveals, applies, hides, and restores the custom lookback draft', () => {
    const handleLookbackChange = vi.fn();
    const { getByLabelText, getByRole, queryByLabelText, queryByRole } = render(
      <I18nProvider initialLocale="en">
        <LookbackControl
          ariaLabel="Task Metrics Lookback minutes"
          lookbackMinutes={15}
          maxLookbackMinutes={1440}
          presets={[5, 10, 15, 30]}
          onLookbackMinutesChange={handleLookbackChange}
        />
      </I18nProvider>,
    );

    expect(queryByLabelText('Lookback minutes')).toBeNull();
    expect(queryByRole('button', { name: 'Apply' })).toBeNull();

    fireEvent.click(getByRole('button', { name: 'Custom' }));
    fireEvent.change(getByLabelText('Lookback minutes'), { target: { value: '45' } });
    fireEvent.click(getByRole('button', { name: 'Apply' }));

    expect(handleLookbackChange).toHaveBeenCalledWith(45);

    fireEvent.click(getByRole('button', { name: '30m' }));
    expect(handleLookbackChange).toHaveBeenCalledWith(30);
    expect(queryByLabelText('Lookback minutes')).toBeNull();

    fireEvent.click(getByRole('button', { name: 'Custom' }));
    expect((getByLabelText('Lookback minutes') as HTMLInputElement).value).toBe('45');
  });
});
