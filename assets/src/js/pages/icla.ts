import { mount } from 'svelte';
import IclaForm from '@/js/components/IclaForm.svelte';

// The server renders the invitation a submitter followed, if any, into the placeholder (icla.html).
const target = document.getElementById('icla-form');
if (target !== null) {
  mount(IclaForm, { target, props: { invitation: target.dataset.invitation ?? '' } });
}
