# PowerContext Hermes Command Companion

This standalone Hermes plugin registers `/pc` during normal plugin discovery,
before Hermes creates its first Agent. It forwards the command to the active
PowerContext Memory Provider once that provider is initialized.

Typing `/pc ` or `/powercontext ` and pressing Tab/Down shows the available
first-level PowerContext commands in Hermes' autocomplete menu.

The companion is installed alongside
[`plugins/powercontext`](../powercontext/README.md) by:

```bash
powercontext setup hermes --source oceanbase/powercontext --ref v0.0.2
```

It requires Hermes Agent v0.20.4 or newer. The companion does not provide
memory storage or lifecycle hooks; those remain owned by the exclusive
`powercontext` Memory Provider.
