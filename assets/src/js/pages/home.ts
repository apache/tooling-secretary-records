import { mount } from 'svelte';
import RoleSummary from '@/js/components/RoleSummary.svelte';

// The server renders the reader and their roles into the placeholder (home.html); the
// island turns them into a sentence. No placeholder means nobody is signed in.
const target = document.getElementById('role-summary');
if (target !== null) {
  const roles: unknown = JSON.parse(target.dataset.roles ?? '[]');
  mount(RoleSummary, {
    target,
    props: {
      uid: target.dataset.uid ?? '',
      roles: Array.isArray(roles) ? roles.filter((role): role is string => typeof role === 'string') : []
    }
  });
}
