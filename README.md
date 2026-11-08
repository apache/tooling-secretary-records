# Secretary Records

The replacement for the Whimsy Secretary Workbench: the tool that processes the
paperwork arriving at `secretary@apache.org` - ICLAs, CCLAs, software grants,
membership applications, emeritus and withdrawal requests.

It takes the same shape as the Board Agenda Tool
([apache/tooling-agenda](https://github.com/apache/tooling-agenda)): Python and
Quart through `asfquart`, Svelte, SQLite, ASF OAuth, and SVN as the source of
record.

- [REQUIREMENTS.md](REQUIREMENTS.md) says what it must do, and
  [WORKBENCH.md](WORKBENCH.md) describes the tool it replaces.
- [CONTRIBUTING.md](CONTRIBUTING.md) gets a development environment running.
- [AGENTS.md](AGENTS.md) is the contract for changing it, for people and agents alike.
- How the tool is used, role by role, is the in-app Documentation page, written
  as markdown in [app/md/documentation/](app/md/documentation/).

The Tooling team is responsible for its development.

## License

This project is licensed under the [Apache License 2.0](LICENSE).
