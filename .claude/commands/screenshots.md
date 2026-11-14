---
description: Capture the screenshots a UI pull request needs and publish them where GitHub will show them
argument-hint: "[pr number] (defaults to the PR for the current branch)"
---

# Screenshots for a pull request

A user-facing UI change must show what it looks like. Take the shots from the end-to-end
spec that exercises the feature rather than by hand, so that the picture in the pull
request is of the code as it runs and stays reproducible:

1. Find or add a `page.screenshot(...)` in the spec covering this change
   (see [e2e/README.md](../../e2e/README.md)), and run that spec with
   `scripts/e2e-container.sh <filter>`. The files land in `e2e/screenshots/`, because the
   suite writes into the bind-mounted `e2e/` directory.
2. Look at each one before publishing it. A screenshot of the wrong state is worse than
   none, and this is the only point at which anybody checks.
3. Publish and paste what it prints:

   ```sh
   scripts/pr-screenshots.sh $ARGUMENTS e2e/screenshots/<shot>.png
   ```

   It creates a secret gist, pushes the images in, verifies each url the way a browser
   will, and prints the markdown. Add its closing line to the pull request description
   too, so the gist gets deleted when the pull request closes.

Every size matters, not just the desktop one: the tablet width is where this layout is
tightest. If the change affects layout, capture it at more than one viewport.
