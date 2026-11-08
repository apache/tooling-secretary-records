<script lang="ts">
  import { describeRoles } from '@/js/libs/roles';

  let { uid, roles }: { uid: string; roles: string[] } = $props();

  // One string rather than markup around the uid: prettier breaks a line between an element
  // and the punctuation after it, and Svelte draws that line break as a space.
  const sentence = $derived(`Signed in as ${uid}, with ${describeRoles(roles)}.`);
</script>

<p class="mb-0" data-testid="role-summary">{sentence}</p>
