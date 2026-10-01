Change 1 — Remove the spacing between individual carnet photos

Currently, when multiple copies of the same photo are placed on the sheet, there is a visible gap between adjacent photos.

Change this so that adjacent carnet photos have zero spacing between them.

For example, instead of:

[ PHOTO ] [ PHOTO ] [ PHOTO ] [ PHOTO ] [ PHOTO ] [ PHOTO ]

the layout should be:

[ PHOTO ][ PHOTO ][ PHOTO ][ PHOTO ][ PHOTO ][ PHOTO ]

Likewise for multiple rows:

[ PHOTO ][ PHOTO ][ PHOTO ][ PHOTO ][ PHOTO ][ PHOTO ]
[ PHOTO ][ PHOTO ][ PHOTO ][ PHOTO ][ PHOTO ][ PHOTO ]

The purpose is practical: the user physically cuts the printed sheet into individual carnet photos. Unnecessary gaps create additional cutting work.

Important
This should be a true 0-gap layout, not merely a smaller configurable gap.
Preserve the existing paper margins and all other layout settings unless they are directly affected by this change.
Do not change the actual configured photo dimensions.
Do not alter the orientation or scaling behavior.
Do not introduce crop/bleed behavior unless the existing implementation already uses it.
The photos should simply occupy adjacent positions with no intentional inter-photo whitespace.
Change 2 — Support multiple source images in one job

Currently the tool accepts one source image and generates a configurable number of copies of that image.

The current conceptual model is:

Source image
     ↓
quantity = "x" (default is 6)
     ↓
PDF containing "x" number of copies

Change this to support multiple source images, each with its own quantity.

The conceptual model should become:

Source image A → quantity "x"
Source image B → quantity "y"
Source image C → quantity "z"
             ↓
        layout engine
             ↓
          PDF sheet(s)

The default quantity for a newly added image should remain 6, preserving the current behavior for the common single-image use case.

Example

The user could add:

Person A — 6 copies
Person B — 6 copies
Person C — 3 copies

The tool should automatically place all 15 requested photos into the available sheet space.

If they do:

Person A — 2 copies
Person B — 1 copy
Person C — 6 copies

the PDF should contain exactly those requested quantities.

Layout behavior for multiple images

The layout engine should treat the requested photos as a collection of individual photo instances.

For example:

A × 6
B × 4
C × 2

becomes effectively:

A A A A A A B B B B C C

which is then packed according to the tool's existing layout/orientation/paper-size rules.

Use the existing layout system rather than creating a separate layout mechanism for multiple images.

A sensible default ordering is sequential/row-major placement: finish the requested copies of the first image, then continue with the next image.

If the requested photos do not fit on one sheet, continue onto additional PDF pages using the same layout rules.

Preserve existing controls

Do NOT remove or redesign the existing controls for:

paper/sheet size
orientation
photo dimensions
number of copies
layout/positioning
any existing margins or other layout settings

The new functionality should integrate with the current tool rather than replace its layout system.

The existing single-image workflow must continue to work:

Add one image
Leave quantity at default 6
Generate PDF

This should produce the same kind of result as today, except that adjacent photos now have zero spacing.

UI / input model

Wherever the current interface allows the user to select the source image and quantity, extend it so the user can have a list of image/quantity entries.

Conceptually:

[Image A]   Quantity: [6]
[Image B]   Quantity: [6]
[Image C]   Quantity: [2]

with the ability to:

add another image
set the quantity independently for each image
remove an image from the list

A newly added image should default to quantity 6.

Do not make the user specify a quantity for every image if they are happy with the default.

Acceptance criteria

The implementation is complete when all of the following are true:

A single image can still be processed exactly as before.
The default quantity for a source image remains 6.
The user can add multiple different source images to the same job.
Each source image has an independent copy quantity.
The generated PDF contains the requested number of copies of every source image.
Multiple source images can occupy the same physical sheet.
If necessary, the layout continues onto additional PDF pages.
Existing paper size, orientation, photo size, and layout controls continue to work.
Adjacent photos have zero intentional spacing between them.
Existing margins and sheet boundaries are preserved.
No unnecessary redesign of the existing tool/UI should be introduced.

The goal is a small, targeted enhancement to the existing tool, not a replacement of its current layout system.
