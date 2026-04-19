#include "mfcish/Dialog.h"

namespace mfcish {

BEGIN_MESSAGE_MAP(Dialog, /*base=*/void)
END_MESSAGE_MAP()

int Dialog::onOk() { return 0; }
int Dialog::onCancel() { return 1; }

}  // namespace mfcish
