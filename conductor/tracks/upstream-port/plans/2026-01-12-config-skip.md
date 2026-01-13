# YAML Configuration System Refactor

**Status:** SKIPPED
**Reason:** superseded
**Date:** 2026-01-12

## Upstream Commits

- [ ] `13eff115` - New design for illumination/acquisition configs (53 files)
- [ ] `3866b183` - Remove legacy config managers
- [ ] `98c50432` - Fix stale references

## Skip Justification

arch_v2 keeps its own ChannelConfigurationManager which works differently from upstream. The hierarchical YAML configuration system with machine_configs and user_profiles doesn't apply to this architecture.

The arch_v2 approach uses:
- Existing ChannelConfigurationManager for channel configs
- INI files for machine configuration
- Different data flow patterns

Porting this would require a complete rewrite of the configuration system, which is out of scope and unnecessary for arch_v2.
