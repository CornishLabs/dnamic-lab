#!/usr/bin/env python3
# ARTIQ applet (PyQt6 + pyqtgraph) with:
# - ImageView + histogram panel
# - magma colormap (few control points => sane histogram handles)
# - interactive pixel-snapped ROIs that write back to the 'rois' dataset
# - single top-row toolbar: [Autoscale] [Auto once]  <x,y,val>
# - autoscale uses min/max; histogram bounds stay in sync

import numpy as np

import PyQt6  # ensure pyqtgraph binds to Qt6
from PyQt6 import QtWidgets, QtCore, QtGui


import pyqtgraph as pg
from artiq.applets.simple import SimpleApplet

pg.setConfigOptions(imageAxisOrder='row-major')  # width = n_x, height = n_y


def _simple_colormap(name="magma", stops=6) -> pg.ColorMap:
    base = pg.colormap.get(name)  # modern API
    lut = base.getLookupTable(0.0, 1.0, stops)    # (stops x 3 or x4) uint8
    colors = lut[:, :3]                           # Nx3 RGB
    pos = np.linspace(0.0, 1.0, stops)
    return pg.ColorMap(pos, colors)


class ImageWithROIs(pg.ImageView):
    def __init__(self, args, req):
        super().__init__()
        self.args = args
        self.req = req   # <-- keep request interface so we can set datasets

        # Show histogram; hide extra buttons for a clean look.
        if getattr(self.ui, "menuBtn", None):
            self.ui.menuBtn.hide()
        if getattr(self.ui, "roiBtn", None):
            self.ui.roiBtn.hide()

        # NumPy-like coords: (x right, y down); origin top-left
        self.getView().setAspectLocked(True)
        self.getView().invertY(True)

        # Colormap (few control points => few histogram handles)
        self._cmap = _simple_colormap("magma", stops=6)
        self.setColorMap(self._cmap)   # updates both image & histogram

        # State
        self._img_np = None
        self._autoscale = True
        self._roi_items = []        # list[list[pg.RectROI]]
        self._roi_labels = []       # list[list[pg.TextItem]]
        self._internal_update = False  # guard to avoid feedback loops

        # ---- Single top-row toolbar overlay: [Autoscale] [Auto once]  position ----
        vp = self.ui.graphicsView.viewport()
        self._toolbar = QtWidgets.QWidget(vp)
        layout = QtWidgets.QHBoxLayout(self._toolbar)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(8)

        self._chk_autoscale = QtWidgets.QCheckBox("Autoscale", self._toolbar)
        self._chk_autoscale.setChecked(self._autoscale)
        self._btn_auto_once = QtWidgets.QPushButton("Auto once", self._toolbar)

        # Position label (monospace), fixed-ish width
        self._pos_label = QtWidgets.QLabel("", self._toolbar)
        self._pos_label.setStyleSheet("QLabel { font-family: monospace; }")
        self._pos_label.setMinimumWidth(200)

        layout.addWidget(self._chk_autoscale)
        layout.addWidget(self._btn_auto_once)
        layout.addWidget(self._pos_label)

        self._toolbar.setStyleSheet(
            "QWidget { background: rgba(0,0,0,120); color: white; border-radius: 4px; }"
            "QPushButton, QCheckBox, QLabel { color: white; }"
        )
        self._toolbar.move(6, 6)
        self._toolbar.adjustSize()

        # Hooks
        self._chk_autoscale.toggled.connect(self._on_autoscale_toggled)
        self._btn_auto_once.clicked.connect(self._apply_auto_levels_once)

        # Mouse move hook (throttled)
        self._mouse_proxy = pg.SignalProxy(
            self.getView().scene().sigMouseMoved, rateLimit=60, slot=self._on_mouse_moved
        )

        # Keep toolbar pinned at top-left on viewport resize
        vp.installEventFilter(self)

        self.resize(900, 600)
        self.setWindowTitle("Tweezers Image")

    # Keep toolbar in the corner when the view resizes
    def eventFilter(self, obj, ev):
        if obj is self.ui.graphicsView.viewport() and ev.type() == QtCore.QEvent.Type.Resize:
            self._toolbar.move(6, 6)
            self._toolbar.adjustSize()
        return super().eventFilter(obj, ev)
    

    # ----- level helpers (sync image + histogram) ----------------------------
    def _set_levels(self, lo: float, hi: float) -> None:
        self.getImageItem().setLevels((lo, hi))
        if getattr(self.ui, "histogram", None) is not None:
            try:
                self.ui.histogram.setLevels(lo, hi)
            except Exception:
                if hasattr(self.ui.histogram, "region"):
                    self.ui.histogram.region.setRegion((lo, hi))

    def _apply_levels_minmax(self, arr) -> bool:
        if arr is None:
            return False
        lo = float(np.nanmin(arr))
        hi = float(np.nanmax(arr))
        if not np.isfinite(lo) or not np.isfinite(hi) or lo == hi:
            return False
        self._set_levels(lo, hi)
        return True

    def _on_autoscale_toggled(self, state: bool):
        self._autoscale = bool(state)
        if self._autoscale and self._img_np is not None:
            self._apply_levels_minmax(self._img_np)

    def _apply_auto_levels_once(self):
        if self._apply_levels_minmax(self._img_np):
            self._chk_autoscale.setChecked(False)

    # ----- interactive ROIs --------------------------------------------------
    def _clear_roi_items(self):
        vb = self.getView()
        # Remove ROI graphics
        for roig in self._roi_items:
            for it in roig:
                vb.removeItem(it)
        # Remove label graphics
        for labg in self._roi_labels:
            for lb in labg:
                vb.removeItem(lb)
        self._roi_items = []
        self._roi_labels = []

    def _ensure_roi_items(self, rois):
        """Create or update interactive RectROIs to match list of (y0,y1,x0,x1)."""
        if rois is None:
            self._clear_roi_items()
            return

        rois = np.asarray(rois)
        if rois.ndim != 3 or rois.shape[2] != 4:
            return

        vb = self.getView()

        # Build from scratch if list length changed
        rebuild = (len(self._roi_items) != len(rois))
        if rebuild:
            self._clear_roi_items()
            for gi, roi_g in enumerate(rois):
                self._roi_items.append([])
                self._roi_labels.append([])
                for roi_i, (y0, y1, x0, x1) in enumerate(roi_g):
                    pos  = pg.Point(float(x0), float(y0))
                    size = pg.Point(float(x1 - x0), float(y1 - y0))
                    r = pg.RectROI(
                        pos, size,
                        sideScalers=False,
                        rotatable=False,
                        scaleSnap=True, translateSnap=True, snapSize=1.0,
                        pen=pg.mkPen((255, 255, 255, 220), width=2),
                        hoverPen=pg.mkPen((0, 200, 255, 220), width=3),
                    )
                    r.setZValue(10)
                    # When the user finishes moving/resizing, snap + push dataset
                    r.sigRegionChangeFinished.connect(self._on_roi_finished)
                    vb.addItem(r)
                    self._roi_items[gi].append(r)

                    label_text = f"({gi},{roi_i})"
                    # --- does not scale with zoom ---
                    # lbl = pg.TextItem(
                    #     text=label_text,
                    #     anchor=(0, 0),                       
                    #     fill=pg.mkColor(0, 0, 0, 140)       
                    # )
                
                    # --- scales with zoom ---
                    lbl = QtWidgets.QGraphicsSimpleTextItem(label_text)
                    font = QtGui.QFont()
                    font.setPointSizeF(1)
                    font.setWeight(QtGui.QFont.Weight.DemiBold)
                    lbl.setFont(font)
                    lbl.setBrush(QtGui.QBrush(QtGui.QColor(255, 255, 255)))
                    
                    lbl.setParentItem(r)                     
                    lbl.setPos(0, 0)     
                    lbl.setZValue(11)
                    self._roi_labels[gi].append(lbl)
        else:
            # Update positions/sizes without re-creating
            self._internal_update = True
            try:
                for gi, roi_g in enumerate(rois):
                    for roi_i, (y0, y1, x0, x1) in enumerate(roi_g):
                        r = self._roi_items[gi][roi_i]
                        # setPos/setSize with finish=False to avoid extra signals
                        r.setPos(pg.Point(float(x0), float(y0)), finish=False)
                        r.setSize(pg.Point(float(x1 - x0), float(y1 - y0)), finish=False)
            finally:
                self._internal_update = False

    def _on_roi_finished(self, *args):
        """Snap ROI to integer grid, clamp to image, and write back dataset."""
        if self._internal_update:
            return
        if self._img_np is None:
            return

        n_y, n_x = self._img_np.shape[:2]

        # Snap each ROI to integer grid (position and size)
        self._internal_update = True
        try:
            new_list = []
            for roi_g in self._roi_items:
                new_group_list = []
                for r in roi_g:
                    x0f, y0f = r.pos().x(), r.pos().y()
                    wf, hf   = r.size().x(), r.size().y()

                    # Round to nearest pixel (you can switch to floor/ceil if preferred)
                    x0 = int(round(x0f))
                    y0 = int(round(y0f))
                    w  = max(1, int(round(wf)))
                    h = max(1, int(round(hf)))

                    # Clamp inside image bounds
                    x0 = min(max(0, x0), max(0, n_x - 1))
                    y0 = min(max(0, y0), max(0, n_y - 1))
                    x1 = min(x0 + w, n_x)
                    y1 = min(y0 + h, n_y)

                    # Apply snapped geometry back to the ROI (no 'finish' to avoid loops)
                    r.setPos(pg.Point(x0, y0), finish=False)
                    r.setSize(pg.Point(x1 - x0, y1 - y0), finish=False)

                    new_group_list.append((int(y0), int(y1), int(x0), int(x1)))
                new_list.append(new_group_list)
        finally:
            self._internal_update = False

        # Push updated ROI list back to dataset (persist so it survives)
        rois_name = getattr(self.args, "rois", None)
        if rois_name:
            self.req.set_dataset(rois_name, new_list, persist=True)

    # ----- mouse readout (constant-size, top row) ----------------------------
    def _on_mouse_moved(self, evt):
        # Check we have an image
        if self._img_np is None:
            self._pos_label.setText("")
            return
        
        # Check our mouse is in the scene
        pos = evt[0]
        vb = self.getView()
        if not vb.sceneBoundingRect().contains(pos):
            self._pos_label.setText("")
            return
        
        # Map to image pixel
        p = vb.mapSceneToView(pos) 
        x = int(np.floor(p.x()))
        y = int(np.floor(p.y()))
        
        # If in the image, show position & value
        n_y, n_x = self._img_np.shape[:2]
        if 0 <= x < n_x and 0 <= y < n_y:
            val = self._img_np[y, x]
            self._pos_label.setText(f"x={x}, y={y}, val={val}")
        else:
            self._pos_label.setText("")

    # ----- ARTIQ hook --------------------------------------------------------
    def data_changed(self, value, metadata, persist, mods):
        # Update the image only when it actually changes
        img = value.get(self.args.image)
        if img is not None:
            arr = np.array(img)
            first_frame = self._img_np is None

            self._img_np = arr

            self.setImage(
                arr,
                autoRange=False,
                autoLevels=self._autoscale,
                autoHistogramRange=first_frame, # Allow autoHistogramRange on the very first frame so the image appears; after that, keep the range.
            )

        # ROIs — this does NOT touch the image/zoom
        rois_name = getattr(self.args, "rois", None)
        rois = value.get(rois_name)   if rois_name   else None
        self._ensure_roi_items(rois)


def main():
    applet = SimpleApplet(ImageWithROIs)
    applet.add_dataset("image", "2D image dataset")
    applet.add_dataset("rois", "Optional ROI list: (y0,y1,x0,x1)", required=False)
    applet.run()


if __name__ == "__main__":
    main()
