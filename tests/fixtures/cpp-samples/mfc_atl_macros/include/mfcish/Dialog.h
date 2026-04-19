#pragma once

// Simplified reproduction of MFC-style macros. libclang usually
// handles these with -fms-extensions; if it chokes, the L2 tree-sitter
// fallback should still yield method signatures.

#define BEGIN_MESSAGE_MAP(cls, base) /* stub */
#define END_MESSAGE_MAP()             /* stub */
#define DECLARE_DYNAMIC(cls)          /* stub */

namespace mfcish {

class Dialog {
public:
    DECLARE_DYNAMIC(Dialog)
    virtual ~Dialog() = default;
    virtual int onOk();
    virtual int onCancel();
};

}  // namespace mfcish
