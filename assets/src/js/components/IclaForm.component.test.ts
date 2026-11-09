import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/svelte';

import IclaForm from '@/js/components/IclaForm.svelte';
import type { IclaApi, IclaFields } from '@/js/libs/icla';

// The ICLA form. What is valid, and what becomes of a submission, is the server's (tests/test_icla.py);
// what is asked here is that the form sends what was written, asks for what is required, shows what the
// server says beside the field it is about, and offers each way of signing what it needs.

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });

const serving = (answer: Response = json({ received: true, mailed: true }, 201)) =>
  ({
    projects: vi.fn(async () => json([{ id: 'arbustum', name: 'Arbustum', kind: 'pmc' }])),
    document: vi.fn(async (_fields: IclaFields) => new Response('%PDF-1.4', { status: 200 })),
    submit: vi.fn(async (_body: FormData) => answer)
  }) satisfies IclaApi;

const type = async (label: RegExp | string, value: string) => {
  const field = screen.getByLabelText(label) as HTMLInputElement;
  await fireEvent.input(field, { target: { value } });
};

const fillIn = async () => {
  await type('Full name', 'Ada Lovelace');
  await type('Email', 'ada@example.org');
  await type('Postal address', '12 St James Square');
  await type('Country', 'United Kingdom');
};

const sent = (api: ReturnType<typeof serving>): FormData => api.submit.mock.calls[0][0];

afterEach(() => vi.restoreAllMocks());

describe('the ICLA form', () => {
  it('sends what was written, signed by typing the name, and says it was sent', async () => {
    const api = serving();
    render(IclaForm, { invitation: 'inv-42', api });

    await fillIn();
    await type(/Preferred Apache ids/, 'ada, lovelace');
    await type(/Project to notify/, 'arbustum');
    await fireEvent.click(screen.getByLabelText('Type your name to sign'));
    await type('Your full name, to sign', 'Ada Lovelace');
    await fireEvent.click(screen.getByLabelText(/I agree to the terms/));
    await fireEvent.click(screen.getByRole('button', { name: 'Send the ICLA' }));

    expect(await screen.findByRole('heading', { name: 'ICLA sent' })).toBeTruthy();
    const body = sent(api);
    expect(body.get('signing')).toBe('typed');
    expect(body.get('agreed')).toBe('true');
    expect(JSON.parse(body.get('fields') as string)).toMatchObject({
      full_name: 'Ada Lovelace',
      apache_ids: ['ada', 'lovelace'],
      notify_project: 'arbustum',
      invitation: 'inv-42'
    });
  });

  it('asks for what is required and how to sign before sending anything', async () => {
    const api = serving();
    render(IclaForm, { api });

    await fireEvent.click(screen.getByRole('button', { name: 'Send the ICLA' }));

    expect(screen.getByText('Full name is required')).toBeTruthy();
    expect(screen.getByText('Choose how you sign')).toBeTruthy();
    expect(screen.getByLabelText('Full name').getAttribute('aria-invalid')).toBe('true');
    expect(api.submit).not.toHaveBeenCalled();
  });

  it('shows what the server refused beside the field it is about', async () => {
    const api = serving(json({ errors: { email: 'Email is one address, written like name@example.org' } }, 400));
    render(IclaForm, { api });

    await fillIn();
    await fireEvent.click(screen.getByLabelText('Type your name to sign'));
    await fireEvent.click(screen.getByRole('button', { name: 'Send the ICLA' }));

    const said = await screen.findByText(/Email is one address/);
    expect(said.id).toBe('icla-email-error');
    expect(screen.getByLabelText('Email').getAttribute('aria-describedby')).toContain('icla-email-error');
    expect(screen.queryByRole('heading', { name: 'ICLA sent' })).toBeNull();
  });

  it('says what went wrong with the whole where the server does not say a field', async () => {
    render(IclaForm, { api: serving(new Response('busy', { status: 503 })) });

    await fillIn();
    await fireEvent.click(screen.getByLabelText('Type your name to sign'));
    await fireEvent.click(screen.getByRole('button', { name: 'Send the ICLA' }));

    expect(await screen.findByText(/the server answered 503/)).toBeTruthy();
  });

  it('offers the download and the uploads each way of signing needs, and none for a typed name', async () => {
    render(IclaForm, { api: serving() });

    await fireEvent.click(screen.getByLabelText('Type your name to sign'));
    expect(screen.queryByRole('button', { name: 'Download the ICLA' })).toBeNull();

    await fireEvent.click(screen.getByLabelText(/Sign the ICLA as a PDF/));
    expect(screen.getByRole('button', { name: 'Download the ICLA' })).toBeTruthy();
    expect(screen.getByLabelText('The signed ICLA (PDF)')).toBeTruthy();
    expect(screen.queryByLabelText(/Detached signature/)).toBeNull();

    await fireEvent.click(screen.getByLabelText(/Sign the ICLA with PGP/));
    expect(screen.getByLabelText('The ICLA you signed (PDF)')).toBeTruthy();
    expect(screen.getByLabelText(/Detached signature/)).toBeTruthy();
  });

  it('downloads the ICLA filled in only once what it needs is written', async () => {
    const api = serving();
    render(IclaForm, { api });

    await fireEvent.click(screen.getByLabelText(/Sign the ICLA as a PDF/));
    await fireEvent.click(screen.getByRole('button', { name: 'Download the ICLA' }));
    expect(screen.getByText('Country is required')).toBeTruthy();
    expect(api.document).not.toHaveBeenCalled();

    await fillIn();
    URL.createObjectURL = vi.fn(() => 'blob:icla');
    URL.revokeObjectURL = vi.fn();
    await fireEvent.click(screen.getByRole('button', { name: 'Download the ICLA' }));
    await waitFor(() => expect(api.document).toHaveBeenCalledOnce());
    expect(screen.queryByText('Country is required')).toBeNull();
  });

  it('offers the projects BAT lists as the project is typed', async () => {
    render(IclaForm, { api: serving() });

    await waitFor(() => expect(document.querySelector('#icla-projects option[value="Arbustum"]')).not.toBeNull());
  });
});
