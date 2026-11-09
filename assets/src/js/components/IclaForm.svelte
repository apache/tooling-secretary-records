<script lang="ts">
  import {
    apacheIds,
    errorsOf,
    iclaApi,
    projectSent,
    type Errors,
    type IclaApi,
    type IclaFields,
    type Project,
    type Signing
  } from '@/js/libs/icla';

  // The ICLA, filled in and signed one of three ways, and sent to the Secretary. The server holds every
  // rule (app/icla/fields.py, app/icla/submission.py) and says what is wrong by field; this asks for what
  // is required and shows what it is told, beside the field it is about.
  interface Props {
    invitation?: string;
    api?: IclaApi;
  }

  let { invitation = '', api = iclaApi }: Props = $props();

  let fullName = $state('');
  let familyFirst = $state(false);
  let publicName = $state('');
  let email = $state('');
  let postalAddress = $state('');
  let country = $state('');
  let ids = $state('');
  let project = $state('');
  let signing: Signing | undefined = $state(undefined);
  let typedName = $state('');
  let agreed = $state(false);
  let pdfInput: HTMLInputElement | undefined = $state(undefined);
  let signatureInput: HTMLInputElement | undefined = $state(undefined);

  let projects: Project[] = $state([]);
  let errors: Errors = $state({});
  let busy = $state(false);
  let sent: boolean | undefined = $state(undefined);

  $effect(() => {
    api
      .projects()
      .then(async (response) => (response.ok ? ((await response.json()) as Project[]) : []))
      .then((listed) => (projects = listed))
      .catch(() => (projects = []));
  });

  const fields = (): IclaFields => ({
    full_name: fullName,
    family_first: familyFirst,
    public_name: publicName,
    postal_address: postalAddress,
    country,
    email,
    apache_ids: apacheIds(ids),
    notify_project: projectSent(project, projects),
    invitation
  });

  const REQUIRED: [string, () => string, string][] = [
    ['fullName', () => fullName, 'Full name is required'],
    ['email', () => email, 'Email is required'],
    ['postalAddress', () => postalAddress, 'Postal address is required'],
    ['country', () => country, 'Country is required']
  ];

  /** What is missing of what the form requires, before the server is asked anything. */
  const missing = (): Errors => {
    const found: Errors = {};
    for (const [key, value, message] of REQUIRED) {
      if (value().trim() === '') {
        found[key] = message;
      }
    }
    return found;
  };

  /** On leaving a required field: say it is missing, or stop saying so. */
  const checkRequired = (key: string) => {
    const rule = REQUIRED.find(([name]) => name === key);
    if (rule === undefined) {
      return;
    }
    const rest = Object.fromEntries(Object.entries(errors).filter(([name]) => name !== key));
    errors = rule[1]().trim() === '' ? { ...rest, [key]: rule[2] } : rest;
  };

  const download = async () => {
    const wanted = missing();
    if (Object.keys(wanted).length > 0) {
      errors = wanted;
      return;
    }
    const response = await api.document(fields());
    if (!response.ok) {
      errors = await errorsOf(response);
      return;
    }
    errors = {};
    const url = URL.createObjectURL(await response.blob());
    const link = document.createElement('a');
    link.href = url;
    link.download = 'icla.pdf';
    link.click();
    URL.revokeObjectURL(url);
  };

  /** What is sent: the fields, how they were signed, and only the files that way of signing takes. */
  const sending = (how: Signing): FormData => {
    const body = new FormData();
    body.set('fields', JSON.stringify(fields()));
    body.set('signing', how);
    body.set('typedSignature', typedName);
    body.set('agreed', agreed ? 'true' : 'false');
    const pdf = how === 'typed' ? undefined : pdfInput?.files?.[0];
    if (pdf !== undefined) {
      body.set('pdf', pdf);
    }
    const signature = how === 'pgp' ? signatureInput?.files?.[0] : undefined;
    if (signature !== undefined) {
      body.set('signature', signature);
    }
    return body;
  };

  const submit = async (event: SubmitEvent) => {
    event.preventDefault();
    if (busy) {
      return;
    }
    const wanted = missing();
    if (signing === undefined) {
      wanted.signing = 'Choose how you sign';
    }
    if (Object.keys(wanted).length > 0 || signing === undefined) {
      errors = wanted;
      return;
    }
    const body = sending(signing);
    busy = true;
    try {
      const response = await api.submit(body);
      if (response.ok) {
        sent = ((await response.json()) as { mailed: boolean }).mailed;
        errors = {};
      } else {
        errors = await errorsOf(response);
      }
    } catch (e) {
      errors = { form: `The form could not be sent: ${e}. Try again.` };
    }
    busy = false;
  };

  const described = (key: string, hint?: string) =>
    [hint, errors[key] !== undefined ? `icla-${key}-error` : undefined].filter(Boolean).join(' ') || undefined;

  const failed = $derived(Object.keys(errors).length);
</script>

{#snippet error(key: string)}
  {#if errors[key] !== undefined}
    <div class="invalid-feedback d-block" id="icla-{key}-error">{errors[key]}</div>
  {/if}
{/snippet}

{#if sent !== undefined}
  <div class="sr-measure sr-sent" role="status" id="icla-sent">
    <h2>ICLA sent</h2>
    <p>
      The Secretary has your ICLA. Once it is filed, the project you named is told, and an account can be asked for you.
      There is nothing more you need to do now.
    </p>
  </div>
{:else}
  <form class="sr-form sr-measure" novalidate onsubmit={submit}>
    {#if errors.form !== undefined || failed > 1}
      <div class="alert alert-danger" role="alert" id="icla-errors">
        {#if errors.form !== undefined}
          <p class="mb-0">{errors.form}</p>
        {/if}
        {#if failed > 1}
          <p class="mb-0">Some fields need changing before the ICLA can be sent: each says what, below.</p>
        {/if}
      </div>
    {/if}

    <fieldset>
      <legend>Your details</legend>

      <div class="mb-3">
        <label class="form-label sr-required" for="icla-full_name">Full name</label>
        <input
          class="form-control"
          id="icla-full_name"
          autocomplete="name"
          required
          aria-required="true"
          aria-invalid={errors.fullName !== undefined}
          aria-describedby={described('fullName')}
          bind:value={fullName}
          onblur={() => checkRequired('fullName')} />
        {@render error('fullName')}
        <div class="form-check mt-2">
          <input class="form-check-input" type="checkbox" id="icla-family_first" bind:checked={familyFirst} />
          <label class="form-check-label" for="icla-family_first">My family name comes first</label>
        </div>
      </div>

      <div class="mb-3">
        <label class="form-label" for="icla-public_name">Public name</label>
        <input
          class="form-control"
          id="icla-public_name"
          aria-invalid={errors.publicName !== undefined}
          aria-describedby={described('publicName', 'icla-public_name-hint')}
          bind:value={publicName} />
        <div class="sr-hint" id="icla-public_name-hint">
          How your name is shown in public, where not your full name.
        </div>
        {@render error('publicName')}
      </div>

      <div class="mb-3">
        <label class="form-label sr-required" for="icla-email">Email</label>
        <input
          class="form-control"
          id="icla-email"
          type="email"
          autocomplete="email"
          required
          aria-required="true"
          aria-invalid={errors.email !== undefined}
          aria-describedby={described('email')}
          bind:value={email}
          onblur={() => checkRequired('email')} />
        {@render error('email')}
      </div>

      <div class="mb-3">
        <label class="form-label sr-required" for="icla-postal_address">Postal address</label>
        <textarea
          class="form-control"
          id="icla-postal_address"
          rows="3"
          autocomplete="street-address"
          required
          aria-required="true"
          aria-invalid={errors.postalAddress !== undefined}
          aria-describedby={described('postalAddress')}
          bind:value={postalAddress}
          onblur={() => checkRequired('postalAddress')}>
        </textarea>
        {@render error('postalAddress')}
      </div>

      <div class="mb-3">
        <label class="form-label sr-required" for="icla-country">Country</label>
        <input
          class="form-control"
          id="icla-country"
          autocomplete="country-name"
          required
          aria-required="true"
          aria-invalid={errors.country !== undefined}
          aria-describedby={described('country')}
          bind:value={country}
          onblur={() => checkRequired('country')} />
        {@render error('country')}
      </div>

      <div class="mb-3">
        <label class="form-label" for="icla-apache_ids">Preferred Apache ids</label>
        <input
          class="form-control"
          id="icla-apache_ids"
          autocomplete="off"
          aria-invalid={errors.apacheIds !== undefined}
          aria-describedby={described('apacheIds', 'icla-apache_ids-hint')}
          bind:value={ids} />
        <div class="sr-hint" id="icla-apache_ids-hint">
          Up to three, the one you want most first: lowercase letters and digits, beginning with a letter.
        </div>
        {@render error('apacheIds')}
      </div>

      <div class="mb-3">
        <label class="form-label" for="icla-notify_project">Project to notify</label>
        <input
          class="form-control"
          id="icla-notify_project"
          list="icla-projects"
          autocomplete="off"
          aria-invalid={errors.notifyProject !== undefined}
          aria-describedby={described('notifyProject', 'icla-notify_project-hint')}
          bind:value={project} />
        <datalist id="icla-projects">
          {#each projects as listed (listed.id)}
            <option value={listed.name}></option>
          {/each}
        </datalist>
        <div class="sr-hint" id="icla-notify_project-hint">
          The project that invited you, if one did. It is told when your ICLA is on file.
        </div>
        {@render error('notifyProject')}
      </div>
    </fieldset>

    <fieldset aria-describedby={described('signing')}>
      <legend>Sign</legend>
      {#each [['typed', 'Type your name to sign'], ['signed-pdf', 'Sign the ICLA as a PDF, and upload it'], ['pgp', 'Sign the ICLA with PGP, and upload both']] as [value, label] (value)}
        <div class="form-check">
          <input
            class="form-check-input"
            type="radio"
            name="icla-signing"
            id="icla-signing-{value}"
            {value}
            bind:group={signing} />
          <label class="form-check-label" for="icla-signing-{value}">{label}</label>
        </div>
      {/each}
      {@render error('signing')}

      {#if signing === 'typed'}
        <div class="mb-3 mt-3">
          <label class="form-label sr-required" for="icla-typed_signature">Your full name, to sign</label>
          <input
            class="form-control"
            id="icla-typed_signature"
            required
            aria-required="true"
            aria-invalid={errors.typedSignature !== undefined}
            aria-describedby={described('typedSignature', 'icla-typed_signature-hint')}
            bind:value={typedName} />
          <div class="sr-hint" id="icla-typed_signature-hint">
            Exactly as you wrote it above. Typing it signs the ICLA; the Secretary keeps when and from where.
          </div>
          {@render error('typedSignature')}
        </div>
        <div class="form-check">
          <input
            class="form-check-input"
            type="checkbox"
            id="icla-agreed"
            aria-invalid={errors.agreed !== undefined}
            aria-describedby={described('agreed')}
            bind:checked={agreed} />
          <label class="form-check-label sr-required" for="icla-agreed">
            I agree to the terms of the Individual Contributor License Agreement
          </label>
          {@render error('agreed')}
        </div>
      {:else if signing !== undefined}
        <p class="mt-3">
          Download the ICLA filled in with your details, sign it, and upload what you signed.
          <button type="button" class="btn btn-outline-secondary btn-sm ms-1" id="btn-icla-download" onclick={download}>
            Download the ICLA
          </button>
        </p>
        <div class="mb-3">
          <label class="form-label sr-required" for="icla-pdf">
            {signing === 'pgp' ? 'The ICLA you signed (PDF)' : 'The signed ICLA (PDF)'}
          </label>
          <input
            class="form-control"
            type="file"
            accept="application/pdf"
            id="icla-pdf"
            aria-required="true"
            aria-invalid={errors.pdf !== undefined}
            aria-describedby={described('pdf')}
            bind:this={pdfInput} />
          {@render error('pdf')}
        </div>
        {#if signing === 'pgp'}
          <div class="mb-3">
            <label class="form-label sr-required" for="icla-signature">Detached signature (.asc)</label>
            <input
              class="form-control"
              type="file"
              accept=".asc,application/pgp-signature"
              id="icla-signature"
              aria-required="true"
              aria-invalid={errors.signature !== undefined}
              aria-describedby={described('signature', 'icla-signature-hint')}
              bind:this={signatureInput} />
            <div class="sr-hint" id="icla-signature-hint">
              For example <code>gpg --armor --detach-sign icla.pdf</code>
              .
            </div>
            {@render error('signature')}
          </div>
        {/if}
      {/if}
    </fieldset>

    <button type="submit" class="btn btn-primary mt-4" id="btn-icla-send" aria-busy={busy}>
      {busy ? 'Sending' : 'Send the ICLA'}
    </button>
  </form>
{/if}
