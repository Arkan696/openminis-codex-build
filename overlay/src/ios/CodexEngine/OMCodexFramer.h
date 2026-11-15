// SPDX-License-Identifier: GPL-3.0-or-later
#ifndef OM_CODEX_FRAMER_H
#define OM_CODEX_FRAMER_H
#include <stddef.h>
#include <stdbool.h>
typedef bool (*OMCodexLine)(const unsigned char *,size_t,void *);
typedef struct { unsigned char *bytes;size_t length,limit,total,totalLimit;bool failed; } OMCodexFramer;
bool OMCodexFramerInit(OMCodexFramer *,size_t lineLimit,size_t totalLimit);
bool OMCodexFramerFeed(OMCodexFramer *,const void *,size_t,OMCodexLine,void *);
bool OMCodexFramerFinish(OMCodexFramer *);
void OMCodexFramerDestroy(OMCodexFramer *);
#endif
