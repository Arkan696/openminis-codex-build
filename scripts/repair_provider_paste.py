#!/usr/bin/env python3
"""Reviewed native paste rows for two pinned provider pages; no clipboard/network."""
from pathlib import Path
import hashlib
from overlay_lib import Refused

PINNED = {
    'Views/Providers/AddProviderView.swift': '6957c50bf2dc8a3df92879f416ee6b283a21f437',
    'Views/Providers/ProviderInstanceDetailView.swift': 'f660e20cafc2724639bbd06d9075954b8b66250e',
}

def once(text, old, new):
    if text.count(old) != 1:
        raise Refused('Provider paste source anchor is not unique')
    return text.replace(old, new, 1)

def section(text, begin, end, update):
    if text.count(begin) != 1 or text.count(end) != 1:
        raise Refused('Provider paste function boundary differs')
    start=text.index(begin);stop=text.index(end,start)
    return text[:start]+update(text[start:stop])+text[stop:]

def add_key(text):
    old='''                        .buttonStyle(.plain)
                    }
                } header: {
                    Text("API Key")'''
    new='''                        .buttonStyle(.plain)
                    }
                    OMProviderPasteRow(text: $apiKeyInput, fieldID: pendingInstanceId + ":api-key", secret: true)
                } header: {
                    Text("API Key")'''
    return once(text,old,new)

def add_url(text):
    old='''                .keyboardType(.URL)

            if !enteredViaVoiceTemplate, selectedType != .gemini {'''
    new='''                .keyboardType(.URL)

            OMProviderPasteRow(text: $customBaseURLInput, fieldID: pendingInstanceId + ":base-url", secret: false)

            if !enteredViaVoiceTemplate, selectedType != .gemini {'''
    return once(text,old,new)

def edit_key(text):
    old='''                } label: {
                    Label("Copy API Key", systemImage: "doc.on.doc")
                }
            }
        }
    }

'''
    new='''                } label: {
                    Label("Copy API Key", systemImage: "doc.on.doc")
                }
            }
        }
        OMProviderPasteRow(text: $keyInputText, fieldID: instance.id + ":api-key", secret: true)
    }

'''
    return once(text,old,new)

def edit_url(text):
    old='''                .onDisappear { saveCustomBaseURL(instance) }

            // [T-mimo-shadow-voice]'''
    new='''                .onDisappear { saveCustomBaseURL(instance) }

            OMProviderPasteRow(text: $editingCustomBaseURL, fieldID: instance.id + ":base-url", secret: false)

            // [T-mimo-shadow-voice]'''
    return once(text,old,new)

def proposed_changes(ios):
    ios=Path(ios);original={}
    # Verify both sources completely before even proposing a change. Caller
    # incorporates these proposals before any disposable source-tree writes.
    for rel,expected in PINNED.items():
        p=ios/rel
        if p.is_symlink() or not p.is_file():raise Refused('Missing regular provider source: '+rel)
        data=p.read_bytes()
        actual=hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()
        if actual != expected:raise Refused('Fixed provider source blob differs: '+rel)
        original[rel]=data.decode('utf-8')
    add=original['Views/Providers/AddProviderView.swift']
    add=section(add,'    private var apiKeySection: some View {','    private var oauthSection: some View {',add_key)
    add=section(add,'    private var customBaseURLSection: some View {','    private var defaultBaseURL: String {',add_url)
    edit=original['Views/Providers/ProviderInstanceDetailView.swift']
    edit=section(edit,'    private func apiKeyCredentialView(_ instance: ProviderInstance) -> some View {','    private func oauthCredentialView(_ instance: ProviderInstance) -> some View {',edit_key)
    edit=section(edit,'    private func customBaseURLSection(_ instance: ProviderInstance) -> some View {','    // MARK: - Custom User-Agent',edit_url)
    result={list(PINNED)[0]:add,list(PINNED)[1]:edit}
    for rel,text in result.items():
        if text.count('OMProviderPasteRow(')!=2:raise Refused('Expected exactly two paste rows per provider page')
    return result
