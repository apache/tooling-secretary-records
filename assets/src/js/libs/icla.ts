// The ICLA submission form's side of the server's routes (app/client/icla.py): what it sends, what it is
// told, and the rules worth asking of it without a page.

export interface Project {
  id: string;
  name: string;
  kind: 'pmc' | 'podling';
}

export type Signing = 'typed' | 'signed-pdf' | 'pgp';

export interface IclaFields {
  full_name: string;
  family_first: boolean;
  public_name: string;
  postal_address: string;
  country: string;
  email: string;
  apache_ids: string[];
  notify_project: string;
  invitation: string;
}

/** What is wrong, by the form's field; `form` for what is wrong with the whole. */
export type Errors = Record<string, string>;

/** The ids as typed - split on commas and spaces, empty ones dropped. */
export const apacheIds = (typed: string): string[] =>
  typed
    .split(/[\s,]+/)
    .map((id) => id.trim())
    .filter((id) => id !== '');

/** The project as the form sends it: its id where the name typed is one the list offers, as typed otherwise. */
export const projectSent = (typed: string, projects: Project[]): string => {
  const wanted = typed.trim().toLowerCase();
  const found = projects.find((p) => p.name.toLowerCase() === wanted || p.id === wanted);
  return found?.id ?? typed.trim();
};

/** What the server says is wrong, or one line for the whole where it says nothing readable. */
export const errorsOf = async (response: Response): Promise<Errors> => {
  try {
    const body = (await response.json()) as { errors?: Errors };
    if (body.errors !== undefined && Object.keys(body.errors).length > 0) {
      return body.errors;
    }
  } catch {
    // not JSON: said below
  }
  return { form: `The form could not be sent: the server answered ${response.status}. Try again.` };
};

export interface IclaApi {
  projects: () => Promise<Response>;
  document: (fields: IclaFields) => Promise<Response>;
  submit: (body: FormData) => Promise<Response>;
}

export const iclaApi: IclaApi = {
  projects: () => fetch('/icla/projects'),
  document: (fields) =>
    fetch('/icla/document', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(fields)
    }),
  submit: (body) => fetch('/icla/submit', { method: 'POST', body })
};
